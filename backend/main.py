from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from fastapi.middleware.cors import (
    CORSMiddleware
)

from pydantic import BaseModel
from backend.services.event_adapter import canonical_api_data
from backend.services.extraction_adapter import adapt_baseline, adapt_extraction
from backend.services.file_extractor import MAX_UPLOAD_BYTES, STAIRException, extract_document
from backend.reconstruction.api import router as reconstruction_router
from backend.review.api import router as review_router
from backend.calendar.api import router as calendar_router
from backend.layout.api import router as layout_router
from backend.analysis.api import router as analysis_router

from backend.services.timetable_parser import (
    parse_intranet_response
)

from backend.services.tarumt_client import (
    TARUMTClient,
    TARUMTLoginError,
    TARUMTNavigationError
)


# =========================================================
# Paths
# =========================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


SAMPLE_RESPONSE_FILE = (
    PROJECT_ROOT
    / "data"
    / "samples"
    / "intranet_response.txt"
)


# =========================================================
# FastAPI
# =========================================================

app = FastAPI(
    title="STAIR Timetable API",
    version="0.2.0"
)
app.include_router(reconstruction_router)
app.include_router(review_router)
app.include_router(calendar_router)
app.include_router(layout_router)
app.include_router(analysis_router)


app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],
)


# =========================================================
# Temporary authenticated sessions
#
# LOCAL FYP PROTOTYPE ONLY
#
# Password is NOT stored.
#
# key:
# random STAIR connection token
#
# value:
# TARUMTClient containing TAR portal cookies
# =========================================================

CONNECTED_CLIENTS: dict[
    str,
    TARUMTClient
] = {}


# =========================================================
# Request Models
# =========================================================

class TARUMTLoginRequest(BaseModel):

    student_id: str

    password: str


class TARUMTSessionRequest(BaseModel):

    token: str


# =========================================================
# Root
# =========================================================

@app.get("/")
def root():

    return {
        "app": "STAIR Timetable API",
        "status": "running",
        "version": "0.2.0"
    }


# =========================================================
# Health
# =========================================================

@app.get("/api/health")
def health():

    return {
        "status": "ok",
        "service": "STAIR backend"
    }


@app.post("/api/extract")
async def extract_uploaded_timetable(request: Request, pipeline: str = "evidence", layout_profile: str | None = None):
    """Extract a bounded raw-body upload; filename is supplied in X-Filename."""
    if pipeline not in {"evidence", "B1", "B2", "B2H-v1"}:
        raise HTTPException(status_code=422, detail="pipeline must be evidence, B1, B2, or B2H-v1")
    if layout_profile is not None and (layout_profile not in {'ocr-ruled-grid-s2a-v1','ocr-block-grid-s2b-v1'} or pipeline != 'evidence'):
        raise HTTPException(422, 'Layout profiles require explicit evidence-only application extraction; registered baseline inputs are unchanged')
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Upload exceeds the configured size limit.")
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from error
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Upload exceeds the configured size limit.")
        chunks.append(chunk)
    try:
        def process_upload():
            supplementary = [] if layout_profile else None
            if layout_profile=='ocr-block-grid-s2b-v1':
                from backend.layout.block_capture import BlockCollector
                supplementary = BlockCollector()
            document = extract_document(
                request.headers.get("x-filename", "upload.pdf"),
                request.headers.get("content-type"), b"".join(chunks),
                force_ocr=bool(layout_profile) or pipeline in {"B1", "B2", "B2H-v1"},
                **({'collector':supplementary} if layout_profile else {}),
            )
            if layout_profile:
                from backend.layout.ruled_models import RuledEvidence
                from backend.layout.block_models import BlockEvidence
                packet = adapt_extraction(document)
                evidence_type = BlockEvidence if layout_profile=='ocr-block-grid-s2b-v1' else RuledEvidence
                sidecar = evidence_type(source_document_id=document.source_id, content_sha256=document.checksum,
                                       pages=tuple(supplementary))
                return {'success':True,'pipeline':pipeline,'data':packet.model_dump(mode='json'),
                        'layout_profile':layout_profile,'layout_evidence':sidecar.model_dump(mode='json')}
            return adapt_extraction(document) if pipeline == "evidence" else adapt_baseline(document, pipeline)
        packet = await run_in_threadpool(process_upload)
    except STAIRException as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error), "diagnostic_code": getattr(error, "diagnostic_code", None)}) from error
    except ValueError as error:
        if layout_profile:
            prefix='S2B' if layout_profile=='ocr-block-grid-s2b-v1' else 'S2A'
            raise HTTPException(422, prefix+'_EXTRACTION_REJECTED: unsupported input or bounded evidence limit exceeded') from None
        raise HTTPException(status_code=422, detail="Invalid extracted event/evidence contract.") from None
    if layout_profile:return packet
    return {"success": True, "pipeline": pipeline, "data": packet.model_dump(mode="json")}


# =========================================================
# LIVE TAR UMT CONNECT
# =========================================================

@app.post("/api/intranet/connect")
def connect_tarumt(
    credentials: TARUMTLoginRequest
):

    client = TARUMTClient()

    try:

        # ---------------------------------------------
        # Login to official TAR UMT Intranet
        # ---------------------------------------------

        client.login(
            credentials.student_id,
            credentials.password
        )

        # ---------------------------------------------
        # Fetch live latest timetable
        # ---------------------------------------------

        html_content = (
            client
            .fetch_latest_timetable_html()
        )

        parsed = (
            parse_intranet_response(
                html_content
            )
        )
        data = canonical_api_data(parsed)

        # ---------------------------------------------
        # Generate random local connection token
        #
        # Browser receives this token.
        # Password is discarded after this request.
        # ---------------------------------------------

        token = uuid4().hex

        CONNECTED_CLIENTS[
            token
        ] = client

        return {

            "success": True,

            "connection_token":
                token,

            "student_name":
                client.student_name,

            "source":
                "tarumt_intranet_live",

            "timetable_url":
                client.last_timetable_url,

            "data":
                data
        }

    except TARUMTLoginError as error:

        client.close()

        raise HTTPException(
            status_code=401,
            detail="TAR UMT authentication failed or session expired. Reconnect with valid credentials."
        )

    except TARUMTNavigationError as error:

        client.close()

        raise HTTPException(
            status_code=502,
            detail="TAR UMT timetable retrieval failed. Please retry explicitly."
        )

    except Exception as error:

        client.close()

        raise HTTPException(
            status_code=500,
            detail="TAR UMT processing failed. No timetable success was confirmed."
        )


# =========================================================
# REFRESH LIVE TIMETABLE
#
# No Student ID / Password required again while
# authenticated session remains valid.
# =========================================================

@app.post("/api/intranet/timetable")
def refresh_tarumt_timetable(
    request: TARUMTSessionRequest
):

    client = (
        CONNECTED_CLIENTS
        .get(request.token)
    )

    if client is None:

        raise HTTPException(
            status_code=401,
            detail=(
                "STAIR Intranet session "
                "does not exist or expired."
            )
        )

    try:

        html_content = (
            client
            .fetch_latest_timetable_html()
        )

        parsed = (
            parse_intranet_response(
                html_content
            )
        )
        data = canonical_api_data(parsed)

        return {

            "success": True,

            "source":
                "tarumt_intranet_live",

            "student_name":
                client.student_name,

            "data":
                data
        }

    except TARUMTLoginError as error:

        client.close()

        CONNECTED_CLIENTS.pop(
            request.token,
            None
        )

        raise HTTPException(
            status_code=401,
            detail="TAR UMT session expired. Reconnect when needed."
        )

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail="TAR UMT timetable refresh failed. Please retry explicitly."
        )


# =========================================================
# DISCONNECT
# =========================================================

@app.post("/api/intranet/disconnect")
def disconnect_tarumt(
    request: TARUMTSessionRequest
):

    client = (
        CONNECTED_CLIENTS.pop(
            request.token,
            None
        )
    )

    if client:

        client.close()

    return {
        "success": True,
        "message": (
            "TAR UMT session disconnected."
        )
    }


# =========================================================
# Keep OLD saved response temporarily for debugging.
#
# Once LIVE connection works, delete this endpoint.
# =========================================================

@app.get(
    "/api/intranet/timetable/sample"
)
def sample_timetable():

    if not SAMPLE_RESPONSE_FILE.exists():

        raise HTTPException(
            status_code=404,
            detail="Sample response missing."
        )

    html_content = (
        SAMPLE_RESPONSE_FILE
        .read_text(
            encoding="utf-8",
            errors="ignore"
        )
    )

    parsed = (
        parse_intranet_response(
            html_content
        )
    )

    return {

        "success": True,

        "source":
            "saved_response",

        "data":
            canonical_api_data(parsed)
    }
