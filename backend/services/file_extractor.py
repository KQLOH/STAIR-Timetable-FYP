from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import mimetypes
import re

from backend.models.extraction import BoundingBox, DocumentEvidence, EvidencePage, EvidenceRegion
from backend.services.ocr_runtime import OCRRuntimeError, recognize, confidence as ocr_confidence


class STAIRException(ValueError):
    """Safe extraction failure that can be returned by the upload API."""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
PDF_EXTENSIONS = {".pdf"}
APPROVED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | PDF_EXTENSIONS
PARSER_VERSION = "evidence-adapter-m3.1"
GRID_TIME_RE = re.compile(
    r"(\d{1,2}(?:[:.-]\d{2})?)\s*(?:-|–|—|to)\s*(\d{1,2}(?:[:.-]\d{2})?)",
    re.I,
)
GRID_DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")


def _region(source_id: str, page: int, line: int, text: str, adapter: str,
            confidence: float | None = None, bbox: BoundingBox | None = None,
            group: str | None = None) -> EvidenceRegion:
    return EvidenceRegion(
        evidence_id=f"evd-{source_id}-{page}-{line}-{uuid4().hex[:8]}",
        page=page, line=line, group=group, text=text, adapter=adapter,
        confidence=confidence, bbox=bbox,
    )


def _decode_text(content: bytes) -> str:
    if not content or b"\x00" in content[:4096]:
        raise STAIRException("EMPTY_OR_BINARY_FILE", "The text file is empty or contains binary data.", 422)
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = content.decode(encoding)
            if text.strip():
                return text
        except UnicodeDecodeError:
            continue
    raise STAIRException("INVALID_TEXT_ENCODING", "The text file encoding is not supported.", 422)


def _extract_text(source_id: str, content: bytes) -> list[EvidencePage]:
    text = _decode_text(content)
    regions = []
    group_index = 1
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            group_index += 1
            continue
        regions.append(_region(
            source_id, 1, line_number, raw.rstrip(), "plain-text", confidence=None,
            group=f"block-{group_index}",
        ))
    return [EvidencePage(page=1, text=text, regions=regions)]


def _collapse_line_indices(indices: list[int]) -> list[int]:
    if not indices:
        return []
    groups = [[indices[0]]]
    for value in indices[1:]:
        if value <= groups[-1][-1] + 2:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group) / len(group)) for group in groups]


def _clock_minutes(value: str) -> int | None:
    cleaned = value.replace(".", ":").replace("-", ":")
    parts = cleaned.split(":", 1)
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) == 2 else 0
    except ValueError:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def _ordered_grid_ranges(raw_ranges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Resolve afternoon headers such as 11:30-1:00 without inventing dates."""
    result: list[tuple[str, str]] = []
    previous_start = -1
    for raw_start, raw_end in raw_ranges:
        start, end = _clock_minutes(raw_start), _clock_minutes(raw_end)
        if start is None or end is None:
            continue
        while start <= previous_start and start + 12 * 60 < 24 * 60:
            start += 12 * 60
        while end <= start and end + 12 * 60 < 24 * 60:
            end += 12 * 60
        if end <= start or end >= 24 * 60:
            continue
        result.append((f"{start // 60:02d}:{start % 60:02d}", f"{end // 60:02d}:{end % 60:02d}"))
        previous_start = start
    return result


def _longest_dark_run(row) -> int:
    longest = current = 0
    for dark in row:
        if bool(dark):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _grid_regions(source_id: str, image, page_number: int, adapter: str, data: dict) -> list[EvidenceRegion]:
    """Recover event cells from a clearly ruled timetable image.

    Long table lines form a small adjacency graph. Missing line segments join
    base slots into merged course cells, preserving spatial relationships that
    ordinary OCR line grouping loses.
    """
    try:
        import numpy as np
    except ImportError:
        return []
    pixels = np.asarray(image.convert("L"))
    dark = pixels < 90
    height, width = dark.shape
    vertical = _collapse_line_indices(
        np.where(dark.sum(axis=0) >= height * 0.68)[0].astype(int).tolist()
    )
    horizontal = _collapse_line_indices(
        np.where(dark.sum(axis=1) >= width * 0.68)[0].astype(int).tolist()
    )
    if len(vertical) < 4 or len(horizontal) < 3:
        return []
    vertical = [value for value in vertical if 0 <= value < width]
    horizontal = [value for value in horizontal if 0 <= value < height]
    slot_count = len(vertical) - 2  # first column contains weekday labels
    day_count = min(len(GRID_DAYS), len(horizontal) - 2)
    if slot_count < 2 or day_count < 1:
        return []

    words = []
    for index, raw in enumerate(data.get("text", [])):
        value = str(raw).strip()
        if not value:
            continue
        left, top = int(data["left"][index]), int(data["top"][index])
        word_width, word_height = int(data["width"][index]), int(data["height"][index])
        words.append({
            "text": value,
            "confidence": max(0.0, min(1.0, float(data["conf"][index]) / 100)),
            "x0": left, "x1": left + word_width, "y0": top, "y1": top + word_height,
            "cx": left + word_width / 2, "cy": top + word_height / 2,
            "line": (data["block_num"][index], data["par_num"][index], data["line_num"][index]),
        })

    header_words = [word for word in words if horizontal[0] < word["cy"] < horizontal[1]]
    header_text = " ".join(word["text"] for word in sorted(header_words, key=lambda word: (word["cx"], word["cy"])))
    parsed_ranges = _ordered_grid_ranges(GRID_TIME_RE.findall(header_text))
    if len(parsed_ranges) < slot_count:
        per_slot = []
        for slot in range(slot_count):
            left, right = vertical[slot + 1], vertical[slot + 2]
            text = " ".join(
                word["text"] for word in sorted(header_words, key=lambda word: (word["cy"], word["cx"]))
                if left < word["cx"] < right
            )
            match = GRID_TIME_RE.search(text)
            if match:
                per_slot.append(match.groups())
        parsed_ranges = _ordered_grid_ranges(per_slot)
    if len(parsed_ranges) < slot_count:
        return []

    def vertical_bar(x: int, top: int, bottom: int) -> bool:
        if bottom - top < 4:
            return False
        band = dark[top + 2:bottom - 2, max(0, x - 2):min(width, x + 3)]
        return bool(band.size and np.mean(np.any(band, axis=1)) >= 0.60)

    def horizontal_bar(y: int, left: int, right: int) -> bool:
        if right - left < 4:
            return False
        band = dark[max(0, y - 2):min(height, y + 3), left + 2:right - 2]
        return bool(band.size and np.mean(np.any(band, axis=0)) >= 0.60)

    regions: list[EvidenceRegion] = []
    region_number = 1
    minimum_rule = max(45, round((vertical[2] - vertical[1]) * 0.35))
    for day_index in range(day_count):
        row_top, row_bottom = horizontal[day_index + 1], horizontal[day_index + 2]
        inner_candidates = [
            y for y in range(row_top + 5, row_bottom - 4)
            if _longest_dark_run(dark[y]) >= minimum_rule
        ]
        y_bounds = [row_top] + [
            y for y in _collapse_line_indices(inner_candidates)
            if row_top + 6 < y < row_bottom - 6
        ] + [row_bottom]
        y_bounds = sorted(set(y_bounds))
        tiles = [(x_index, y_index) for x_index in range(slot_count) for y_index in range(len(y_bounds) - 1)]
        unseen = set(tiles)
        components: list[set[tuple[int, int]]] = []
        while unseen:
            seed = unseen.pop()
            component = {seed}
            queue = [seed]
            while queue:
                x_index, y_index = queue.pop()
                neighbours = []
                if x_index > 0 and not vertical_bar(vertical[x_index + 1], y_bounds[y_index], y_bounds[y_index + 1]):
                    neighbours.append((x_index - 1, y_index))
                if x_index + 1 < slot_count and not vertical_bar(vertical[x_index + 2], y_bounds[y_index], y_bounds[y_index + 1]):
                    neighbours.append((x_index + 1, y_index))
                if y_index > 0 and not horizontal_bar(y_bounds[y_index], vertical[x_index + 1], vertical[x_index + 2]):
                    neighbours.append((x_index, y_index - 1))
                if y_index + 1 < len(y_bounds) - 1 and not horizontal_bar(y_bounds[y_index + 1], vertical[x_index + 1], vertical[x_index + 2]):
                    neighbours.append((x_index, y_index + 1))
                for neighbour in neighbours:
                    if neighbour in unseen:
                        unseen.remove(neighbour)
                        component.add(neighbour)
                        queue.append(neighbour)
            components.append(component)

        for component in components:
            min_x = min(tile[0] for tile in component)
            max_x = max(tile[0] for tile in component)
            min_y = min(tile[1] for tile in component)
            max_y = max(tile[1] for tile in component)
            left, right = vertical[min_x + 1], vertical[max_x + 2]
            top, bottom = y_bounds[min_y], y_bounds[max_y + 1]
            cell_words = [
                word for word in words
                if left + 3 < word["cx"] < right - 3 and top + 3 < word["cy"] < bottom - 3
            ]
            if not cell_words:
                continue
            grouped: dict[tuple, list[dict]] = {}
            for word in cell_words:
                grouped.setdefault(word["line"], []).append(word)
            cell_lines = []
            for line_words in sorted(grouped.values(), key=lambda items: min(item["cy"] for item in items)):
                cell_lines.append(" ".join(item["text"] for item in sorted(line_words, key=lambda item: item["cx"])))
            cell_text = "\n".join(cell_lines).strip()
            if not re.search(r"[A-Za-z]{3}", cell_text):
                continue
            start_time = parsed_ranges[min_x][0]
            end_time = parsed_ranges[max_x][1]
            day = GRID_DAYS[day_index]
            evidence_text = f"{day} {start_time} - {end_time} | {cell_text}\nweekly"
            confidence = sum(word["confidence"] for word in cell_words) / len(cell_words)
            regions.append(_region(
                source_id, page_number, region_number, evidence_text,
                f"{adapter}-grid-cell", confidence,
                BoundingBox(x0=float(left), y0=float(top), x1=float(right), y1=float(bottom)),
                group=f"grid-cell-{day_index + 1}-{min_x + 1}-{max_x + 1}-{region_number}",
            ))
            region_number += 1
    return regions


def _ocr_image(source_id: str, image, page_number: int, adapter: str, *, collector=None, transform=None) -> EvidencePage:
    if collector is not None:
        if hasattr(collector, 'check_image'):
            collector.check_image(image)
        else:
            from backend.layout.ruled_capture import check_image
            check_image(image, collector)
    try:
        data, runtime = recognize(image)
    except OCRRuntimeError as error:
        failure = STAIRException("OCR_UNAVAILABLE", str(error), 503)
        failure.diagnostic_code = error.code
        raise failure from error
    # Preserve recognized source text. The old grid helper injects a weekday,
    # normalized times and "weekly"; it is retained but excluded from this route.

    lines: dict[tuple[int, int, int], list[tuple]] = {}
    for index, text in enumerate(data.get("text", [])):
        value = str(text).strip()
        if not value:
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        scores = data.get("conf", [])
        confidence = ocr_confidence(scores[index] if index < len(scores) else None)
        lines.setdefault(key, []).append((
            value, confidence, data["left"][index], data["top"][index],
            data["width"][index], data["height"][index],
        ))
    regions = []
    for line_number, (_, words) in enumerate(sorted(lines.items()), 1):
        text = " ".join(word[0] for word in words)
        x0 = min(word[2] for word in words)
        y0 = min(word[3] for word in words)
        x1 = max(word[2] + word[4] for word in words)
        y1 = max(word[3] + word[5] for word in words)
        confidence = sum(word[1] for word in words) / len(words) if all(word[1] is not None for word in words) else None
        regions.append(_region(
            source_id, page_number, line_number, text, adapter, confidence,
            BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1),
            group=f"page-{page_number}-line-{line_number}",
        ))
    if collector is not None and hasattr(collector, 'capture_page'):
        collector.capture_page(source_id, image, page_number, adapter, data, runtime, regions,
                               transform or {'operation':'RGB conversion only','scale_x':1,'scale_y':1,
                                             'rotation_applied_degrees':0})
    if not regions:
        raise STAIRException("NO_OCR_TEXT", "No usable timetable text was found in the image.", 422)
    if collector is not None and not hasattr(collector, 'capture_page'):
        from backend.layout.ruled_capture import capture
        collector.append(capture(source_id, image, page_number, adapter, data, runtime, regions,
            transform or {'operation':'RGB conversion only','scale_x':1.0,'scale_y':1.0,
                          'rotation_applied_degrees':0,'exif_rotation_applied':False}))
    return EvidencePage(
        page=page_number, width=float(image.width), height=float(image.height),
        text="\n".join(region.text for region in regions), regions=regions, runtime=runtime,
    )


def _extract_image(source_id: str, content: bytes, *, collector=None) -> list[EvidencePage]:
    try:
        from PIL import Image, UnidentifiedImageError
        image = Image.open(BytesIO(content))
        if collector is not None:
            if hasattr(collector, 'check_image'):
                collector.check_image(image)
            else:
                from backend.layout.ruled_capture import check_image
                check_image(image, collector)
        image.verify()
        image = Image.open(BytesIO(content))
        s2a_transform = None if collector is None else {
            'operation':'RGB conversion only','source_mode':image.mode,
            'source_width':image.width,'source_height':image.height,
            'source_exif_orientation':image.getexif().get(274),
            'source_space':'PIXELS','target_space':'PIXELS','scale_x':1.0,'scale_y':1.0,
            'rotation_applied_degrees':0,'exif_rotation_applied':False}
        image = image.convert("RGB")
    except ImportError as error:
        raise STAIRException("IMAGE_EXTRACTION_UNAVAILABLE", "Image extraction requires optional Pillow support.", 503) from error
    except (UnidentifiedImageError, OSError) as error:
        raise STAIRException("INVALID_IMAGE", "The uploaded file is not a valid PNG or JPEG image.", 422) from error
    return [_ocr_image(source_id, image, 1, "tesseract-ocr-1.0", collector=collector, transform=s2a_transform)]


def _extract_pdf(source_id: str, content: bytes, *, force_ocr: bool = False, collector=None) -> list[EvidencePage]:
    if not content.startswith(b"%PDF-"):
        raise STAIRException("MIME_MISMATCH", "The uploaded .pdf file is not a valid PDF.", 422)
    try:
        import pdfplumber
    except ImportError as error:
        raise STAIRException("PDF_EXTRACTION_UNAVAILABLE", "Native PDF extraction requires optional pdfplumber support.", 503) from error
    try:
        pdf = pdfplumber.open(BytesIO(content))
    except Exception as error:
        raise STAIRException("INVALID_PDF", "The PDF cannot be opened safely.", 422) from error
    try:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise STAIRException(
                "PDF_PAGE_LIMIT", f"PDF files may contain at most {MAX_PDF_PAGES} pages.", 413
            )
        pages = []
        if collector is not None:
            from backend.layout.ruled_models import MAX_PAGES, MAX_PIXELS
            if len(pdf.pages)>getattr(collector,'max_pages',MAX_PAGES):
                raise ValueError('S2B_PAGE_CAP' if hasattr(collector,'capture_page') else 'S2A_PAGE_CAP')
        for page_number, page in enumerate(pdf.pages, 1):
            if force_ocr:
                if collector is not None and page.width*page.height*(200/72)**2>MAX_PIXELS:
                    raise ValueError('S2A_PIXEL_CAP')
                try:
                    image = page.to_image(resolution=200).original.convert("RGB")
                except Exception as error:
                    raise STAIRException(
                        "PDF_OCR_UNAVAILABLE", "The configured PDF rasterizer is unavailable.", 503
                    ) from error
                pages.append(_ocr_image(source_id, image, page_number, "pdf-shared-ocr-tesseract-1.0",
                    collector=collector, transform={'operation':'PDF rasterization','dpi':200,
                    'source_space':'PDF_POINTS','target_space':'PIXELS',
                    'source_width':float(page.width),'source_height':float(page.height),
                    'scale_x':image.width/float(page.width),'scale_y':image.height/float(page.height),
                    'pdf_rotation_metadata':page.rotation,'rotation_applied_degrees':0,
                    'orientation_policy':'renderer page orientation; no deskew or inferred rotation'}))
                continue
            words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
            usable = " ".join(word.get("text", "") for word in words).strip()
            if usable:
                grouped: dict[int, list[dict]] = {}
                for word in words:
                    key = round(float(word["top"]) / 4) * 4
                    grouped.setdefault(key, []).append(word)
                regions = []
                for line_number, (_, line_words) in enumerate(sorted(grouped.items()), 1):
                    line_words.sort(key=lambda item: float(item["x0"]))
                    text = " ".join(word["text"] for word in line_words)
                    bbox = BoundingBox(
                        x0=min(float(word["x0"]) for word in line_words),
                        y0=min(float(word["top"]) for word in line_words),
                        x1=max(float(word["x1"]) for word in line_words),
                        y1=max(float(word["bottom"]) for word in line_words),
                    )
                    regions.append(_region(
                        source_id, page_number, line_number, text, "pdf-native-coordinate", None,
                        bbox, group=f"page-{page_number}-row-{line_number}",
                    ))
                pages.append(EvidencePage(
                    page=page_number, width=float(page.width), height=float(page.height),
                    text="\n".join(region.text for region in regions), regions=regions,
                ))
            else:
                try:
                    image = page.to_image(resolution=200).original.convert("RGB")
                except Exception as error:
                    raise STAIRException(
                        "PDF_OCR_UNAVAILABLE", "A raster PDF page requires the configured OCR renderer.", 503
                    ) from error
                pages.append(_ocr_image(source_id, image, page_number, "pdf-raster-tesseract-1.0"))
        if not any(page.regions for page in pages):
            raise STAIRException("EMPTY_PDF", "The PDF contains no extractable timetable content.", 422)
        return pages
    finally:
        pdf.close()


def extract_document(filename: str, declared_media_type: str | None, content: bytes,
                     *, force_ocr: bool = False, collector=None) -> DocumentEvidence:
    safe_name = Path(filename or "upload").name
    extension = Path(safe_name).suffix.lower()
    if extension not in APPROVED_EXTENSIONS:
        raise STAIRException("UNSUPPORTED_FILE_TYPE", "Upload a PDF, PNG/JPEG image, or plain-text file.", 415)
    if not content:
        raise STAIRException("EMPTY_FILE", "The uploaded file is empty.", 422)
    if len(content) > MAX_UPLOAD_BYTES:
        raise STAIRException("FILE_TOO_LARGE", "The uploaded file exceeds the configured size limit.", 413)
    if content.startswith((b"MZ", b"\x7fELF", b"PK\x03\x04")):
        raise STAIRException("EXECUTABLE_OR_ARCHIVE_BLOCKED", "Executable and archive content is not a timetable input.", 415)
    expected_type = "application/pdf" if extension in PDF_EXTENSIONS else (
        "image/png" if extension == ".png" else "image/jpeg" if extension in IMAGE_EXTENSIONS else "text/plain"
    )
    accepted_types = {expected_type, "application/octet-stream"}
    if extension in TEXT_EXTENSIONS:
        accepted_types |= {"text/csv", "text/tab-separated-values", "text/markdown"}
    if declared_media_type and declared_media_type not in accepted_types:
        raise STAIRException("MIME_MISMATCH", "The filename and declared MIME type do not match.", 422)
    content_checksum = sha256(content).hexdigest()
    # The same source bytes deliberately resolve to the same operational ID.
    # Reprocessing therefore replaces its prior extraction instead of creating
    # a second set of verified events.
    source_id = f"src-{content_checksum[:24]}"
    if extension in PDF_EXTENSIONS:
        route_type = "upload_ocr" if force_ocr else "upload_pdf"
        pages, detected_type = _extract_pdf(source_id, content, force_ocr=force_ocr, collector=collector), "application/pdf"
    elif extension in IMAGE_EXTENSIONS:
        route_type, pages = "upload_image", _extract_image(source_id, content, collector=collector)
        detected_type = "image/png" if extension == ".png" else "image/jpeg"
    else:
        if collector is not None:raise ValueError('S2A_REQUIRES_PDF_OR_IMAGE')
        route_type, pages, detected_type = "upload_text", _extract_text(source_id, content), "text/plain"
    return DocumentEvidence(
        source_id=source_id, route_type=route_type, original_label=safe_name,
        media_type=detected_type, checksum=content_checksum,
        parser_version=PARSER_VERSION, pages=pages,
    )
