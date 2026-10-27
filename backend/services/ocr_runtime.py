"""Optional Tesseract runtime with explicit diagnostics; never substitutes PDF text."""

from dataclasses import dataclass, asdict
from pathlib import Path
import importlib
import json
import os
import shutil
import subprocess
from threading import RLock


class OCRRuntimeError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OCRRuntime:
    executable: str
    engine_version: str
    wrapper_version: str
    languages: tuple[str, ...]
    language: str = "eng"
    config: str = "--psm 6"


_LOCK = RLock()
LOCAL_CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "ocr-runtime.local.json"


def configured_executable():
    """A process override takes precedence over the optional project-local path."""
    if "STAIR_TESSERACT_CMD" in os.environ:
        return os.environ["STAIR_TESSERACT_CMD"]
    if not LOCAL_CONFIG_FILE.is_file():
        return None
    try:
        record = json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8-sig"))
        if set(record) != {"tesseract_cmd"} or not isinstance(record["tesseract_cmd"], str) or not record["tesseract_cmd"].strip():
            raise ValueError("Invalid local runtime configuration")
        return record["tesseract_cmd"]
    except (ValueError, TypeError, OSError) as error:
        raise OCRRuntimeError("OCR_CONFIGURATION_INVALID", "config/ocr-runtime.local.json must contain only a nonempty tesseract_cmd string.") from error


def detect_runtime() -> OCRRuntime:
    try:
        wrapper = importlib.import_module("pytesseract")
    except ImportError as error:
        raise OCRRuntimeError("OCR_WRAPPER_MISSING", "Install requirements-ocr.txt in the project .venv.") from error
    explicit = configured_executable()
    if explicit:
        executable = str(Path(explicit).expanduser().resolve())
        if not Path(executable).is_file():
            raise OCRRuntimeError("OCR_EXECUTABLE_PATH_INVALID", "The explicit Tesseract path in STAIR_TESSERACT_CMD or config/ocr-runtime.local.json must name an existing executable.")
    else:
        executable = shutil.which("tesseract")
        if not executable and os.name == "nt":
            for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
                candidate = Path(root) / "Tesseract-OCR" / "tesseract.exe"
                if candidate.is_file():
                    executable = str(candidate)
                    break
        if not executable:
            raise OCRRuntimeError("OCR_EXECUTABLE_MISSING", "Tesseract was not found on PATH or in standard Windows locations. Install it with approval, then set STAIR_TESSERACT_CMD if needed.")
    try:
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10, check=True)
        languages = subprocess.run([executable, "--list-langs"], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise OCRRuntimeError("OCR_INITIALIZATION_FAILED", "The selected Tesseract executable could not initialize; check platform, permissions and runtime libraries.") from error
    available = tuple(line.strip() for line in languages.stdout.splitlines() if line.strip() and not line.startswith("List of available"))
    if "eng" not in available:
        raise OCRRuntimeError("OCR_LANGUAGE_MODEL_MISSING", "The registered English OCR route requires eng.traineddata; check TESSDATA_PREFIX and the installed language files.")
    version_line = version.stdout.splitlines()
    if not version_line or not version_line[0].lower().startswith("tesseract"):
        raise OCRRuntimeError("OCR_INITIALIZATION_FAILED", "The configured executable did not identify itself as Tesseract.")
    return OCRRuntime(executable, version_line[0].strip(), wrapper.__version__, available)


def recognize(image):
    runtime = detect_runtime()
    wrapper = importlib.import_module("pytesseract")
    # pytesseract exposes a process-wide executable setting. Serialize and restore it.
    with _LOCK:
        previous = wrapper.pytesseract.tesseract_cmd
        try:
            wrapper.pytesseract.tesseract_cmd = runtime.executable
            data = wrapper.image_to_data(image, lang=runtime.language, output_type=wrapper.Output.DICT,
                                         config=runtime.config, timeout=30)
        except Exception as error:
            raise OCRRuntimeError("OCR_INVOCATION_FAILED", "Tesseract failed or exceeded its 30-second limit; check image support and runtime configuration.") from error
        finally:
            wrapper.pytesseract.tesseract_cmd = previous
    columns = ("text", "left", "top", "width", "height", "block_num", "par_num", "line_num")
    if not isinstance(data, dict) or not all(isinstance(data.get(key), (list, tuple)) for key in columns):
        raise OCRRuntimeError("OCR_OUTPUT_INVALID", "The wrapper returned incomplete OCR token columns.")
    count = len(data["text"])
    if any(len(data[key]) != count for key in columns):
        raise OCRRuntimeError("OCR_OUTPUT_INVALID", "The OCR token columns have inconsistent lengths.")
    return data, asdict(runtime)


def confidence(value):
    """Tesseract -1, absent, nonfinite or invalid scores remain unknown."""
    try:
        score = float(value)
    except (ValueError, TypeError):
        return None
    return score / 100 if 0 <= score <= 100 else None
