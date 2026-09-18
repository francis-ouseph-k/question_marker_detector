"""Configuration (Requirements NFR-07).

Configuration comes from three places. Later sources override earlier ones:

1. Defaults defined in the classes below.
2. A YAML file (default ``config/default.yaml``; override with ``QMD_CONFIG_FILE``
   or ``--config``).
3. Environment variables / ``.env`` using the ``QMD_`` prefix and ``__`` for
   nesting, e.g. ``QMD_OCR__BACKEND=onnx`` or ``QMD_APP__LOG_LEVEL=DEBUG``.

Command-line options (see ``cli.py``) override all of the above.

Physical sizes are configured in millimetres (``*_mm``) and converted to pixels
with the page's own dpi, because pixel sizes differ from page to page
(Requirements §2.4).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from qmd.errors import ConfigurationError
from qmd.models import PageRole

DEFAULT_CONFIG_FILE = Path("config/default.yaml")


class _Section(BaseModel):
    """Base for config sections: unknown keys are errors (catches typos)."""

    model_config = ConfigDict(extra="forbid")


class AppConfig(_Section):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    output_root: Path = Path("./output")
    # Keep diagnostic artefacts so OCR/detection failures can be analysed later.
    save_page_images: bool = True        # canonical page images (original JPEG bytes when possible)
    save_debug_images: bool = True       # margin region + overlay per answer page
    save_marker_crops: bool = True       # the exact crop fed to the recognizer
    print_discarded: bool = False        # also print DISCARDED candidates on the console


class ProcessingConfig(_Section):
    # Increment when the processing logic or configuration meaning changes.
    # Combined with a hash of the effective config into ``processing_version``.
    config_version: str = "1.1.0"
    workers: int = Field(default=1, ge=1, le=64)
    max_attempts: int = Field(default=2, ge=1, le=10)


class PdfConfig(_Section):
    canonical_dpi: int = Field(default=200, ge=72, le=600)  # used only if a page must be rendered
    # An embedded image counts as "the page scan" if it covers at least this
    # fraction of the page area.
    full_page_image_min_coverage: float = Field(default=0.9, gt=0, le=1.0)


class BookletConfig(_Section):
    template_id: str = "default_ruled_booklet"
    default_role: PageRole = PageRole.ANSWER
    # 1-based page index -> role. Pages not listed get ``default_role``.
    page_roles: dict[int, PageRole] = Field(default_factory=dict)


class LayoutConfig(_Section):
    margin_search_max_fraction: float = Field(default=0.25, gt=0, lt=0.6)
    margin_rule_min_length_fraction: float = Field(default=0.30, gt=0, le=1.0)
    edge_ignore_mm: float = 1.0
    header_search_max_fraction: float = Field(default=0.20, gt=0, lt=0.6)
    line_pitch_min_mm: float = 6.0
    line_pitch_max_mm: float = 13.0
    min_ruled_lines: int = 5
    # When rebuilding the ruled-line grid, a predicted line snaps to a detected
    # line within this fraction of the line pitch (TD §6.1).
    line_snap_tolerance_fraction: float = Field(default=0.25, gt=0, lt=0.5)
    deskew_threshold_deg: float = 0.4
    max_skew_deg: float = 5.0


class RegionConfig(_Section):
    spill_mm: float = 6.0             # how far right of the margin rule the region extends
    top_offset_mm: float = 2.0        # start this far below the header rule
    rule_mask_half_width_mm: float = 0.6
    fallback_region_width_mm: float = 30.0  # used when the margin rule is not found


class InkConfig(_Section):
    enable_colour_separation: bool = True
    # OpenCV HSV: hue 0-179, saturation/value 0-255.
    red_hue_ranges: list[tuple[int, int]] = Field(default_factory=lambda: [(0, 12), (165, 179)])
    green_hue_range: tuple[int, int] = (35, 90)
    min_saturation: int = 70
    min_value: int = 50
    dark_ink_threshold: int = 150      # gray level below which a pixel counts as ink
    grayscale_channel_spread_min: float = 2.0  # 90th pct of (max-min channel) below this -> grayscale scan


class TriageConfig(_Section):
    scale: float = Field(default=0.5, gt=0.1, le=1.0)  # work on a reduced copy for speed
    min_component_area_mm2: float = 1.0
    blank_max_components: int = 2
    sparse_max_components: int = 40


class DetectionConfig(_Section):
    # "margin_components": group ink blobs in the margin (TD §3.2 option B).
    # "ocr_model": run the OCR engine's text detector on tiles (TD §7.1).
    method: Literal["margin_components", "ocr_model"] = "margin_components"
    min_candidate_height_mm: float = 2.5
    max_component_height_mm: float = 25.0
    min_component_area_mm2: float = 0.4
    merge_gap_mm: float = 6.0
    crop_padding_mm: float = 1.0
    # A candidate read as ONE character whose box is much wider than tall is not a
    # handwritten digit (typically a valuer tick in dark ink). See README A-9.
    single_char_max_aspect: float = 1.3
    # ocr_model only:
    tile_height_lines: int = 4
    tile_overlap_lines: int = 1
    nms_iou: float = 0.3


class OcrConfig(_Section):
    backend: Literal["paddle", "onnx"] = "paddle"
    # PaddleOCR (backend=paddle). Model names follow PaddleOCR 3.x.
    det_model_name: str = "PP-OCRv5_mobile_det"
    rec_model_name: str = "PP-OCRv5_mobile_rec"
    det_model_dir: Optional[Path] = None   # local model folder (offline installs)
    rec_model_dir: Optional[Path] = None
    enable_mkldnn: bool = True
    # ONNX (backend=onnx, via RapidOCR). Empty paths = models bundled with the wheel.
    onnx_det_model_path: Optional[Path] = None
    onnx_rec_model_path: Optional[Path] = None
    onnx_rec_keys_path: Optional[Path] = None
    # Shared
    threads_per_worker: int = Field(default=2, ge=1, le=64)
    det_box_thresh: float = 0.4
    det_thresh: float = 0.3
    det_unclip_ratio: float = 1.6
    rec_batch_size: int = Field(default=8, ge=1, le=64)
    calibration_file: Optional[Path] = None  # TD §19.2; None = raw scores (uncalibrated)


class GrammarConfig(_Section):
    version: str = "1.0"
    # Compared after removing '.' and spaces, case-insensitively.
    prefixes: list[str] = Field(
        default_factory=lambda: ["ANSWER", "ANS", "AN", "QUESTION", "QUES", "QNO", "QN", "Q", "NO"]
    )
    max_prefix_residue_chars: int = 2   # unknown letters allowed before the ID (clipped prefix)
    max_noise_digits: int = 1           # leading digits that may be dropped from the ID (TD §10.1)
    max_first_number_digits: int = 3
    max_sub_number_digits: int = 2
    header_lookahead_lines: int = 2     # see README assumption A-7


class ResolutionConfig(_Section):
    version: str = "1.0"
    match_threshold: float = 0.60
    margin_threshold: float = 0.20
    noise_penalty: float = 0.15   # per stripped leading digit
    trim_penalty: float = 0.10    # per trimmed trailing segment
    insert_cost: float = 1.0
    delete_cost: float = 1.0
    default_substitute_cost: float = 1.0
    # [ocr_char, expected_char, cost]. expected_char "" = cheap deletion of ocr_char
    # (typically a closing bracket misread as '1', '7', 'j' or 'l').
    confusions: list[tuple[str, str, float]] = Field(
        default_factory=lambda: [
            ("6", "b", 0.3), ("b", "6", 0.3), ("l", "1", 0.2), ("i", "1", 0.3), ("1", "i", 0.3),
            ("|", "1", 0.2), ("o", "0", 0.2), ("0", "o", 0.3), ("d", "0", 0.5), ("s", "5", 0.3),
            ("5", "s", 0.3), ("z", "2", 0.3), ("2", "z", 0.3), ("g", "9", 0.3), ("q", "9", 0.3),
            ("9", "g", 0.3), ("t", "7", 0.4), ("B", "8", 0.3), ("8", "b", 0.4), ("a", "9", 0.5),
            ("1", "", 0.4), ("7", "", 0.5), ("j", "", 0.4), ("l", "", 0.4), (")", "", 0.1),
        ]
    )


class QuestionStartConfig(_Section):
    max_lines_below: int = 3
    min_ink_ratio: float = 0.004
    start_padding_mm: float = 0.0
    right_margin_mm: float = 5.0
    fallback_offset_mm: float = 5.0


class GateConfig(_Section):
    min_recognition_confidence: float = 0.80
    adjusted_parse_min_confidence: float = 0.92   # when NOISE_STRIPPED or LINE_TRIMMED
    grayscale_min_confidence: float = 0.90
    min_resolution_margin: float = 0.20
    min_question_start_confidence: float = 0.60


class Settings(BaseSettings):
    """Complete, validated configuration."""

    model_config = SettingsConfigDict(
        env_prefix="QMD_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    pdf: PdfConfig = Field(default_factory=PdfConfig)
    booklet: BookletConfig = Field(default_factory=BookletConfig)
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    region: RegionConfig = Field(default_factory=RegionConfig)
    ink: InkConfig = Field(default_factory=InkConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    grammar: GrammarConfig = Field(default_factory=GrammarConfig)
    resolution: ResolutionConfig = Field(default_factory=ResolutionConfig)
    question_start: QuestionStartConfig = Field(default_factory=QuestionStartConfig)
    gate: GateConfig = Field(default_factory=GateConfig)

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.layout.line_pitch_min_mm >= self.layout.line_pitch_max_mm:
            raise ValueError("layout.line_pitch_min_mm must be < layout.line_pitch_max_mm")
        if self.gate.adjusted_parse_min_confidence < self.gate.min_recognition_confidence:
            raise ValueError("gate.adjusted_parse_min_confidence must be >= gate.min_recognition_confidence")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority (first wins): explicit init kwargs > env vars > .env > YAML file.
        return (init_settings, env_settings, dotenv_settings, _YamlSource(settings_cls))

    # ------------------------------------------------------------------ #

    def fingerprint(self) -> str:
        """Short hash of the effective configuration (part of processing_version)."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:10]

    def processing_version(self) -> str:
        return f"{self.processing.config_version}+{self.fingerprint()}"


# The YAML path is chosen at load time; kept module-level so the source can read it.
_yaml_path: Optional[Path] = None


class _YamlSource(PydanticBaseSettingsSource):
    """Reads the YAML configuration file selected by ``load_settings``."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # pragma: no cover
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if _yaml_path is None:
            return {}
        return _read_yaml(_yaml_path)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Configuration file is not valid YAML: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration file must contain a mapping: {path}")
    unknown = set(data) - set(Settings.model_fields)
    if unknown:
        raise ConfigurationError(f"Unknown configuration section(s) in {path}: {sorted(unknown)}")
    return data


def load_settings(config_path: Optional[Path] = None, **overrides: Any) -> Settings:
    """Load and validate settings.

    ``config_path`` > ``QMD_CONFIG_FILE`` env var > ``config/default.yaml`` (if present).
    ``overrides`` are nested dicts, e.g. ``load_settings(ocr={"backend": "onnx"})``.
    """
    global _yaml_path
    if config_path is None:
        env_path = os.environ.get("QMD_CONFIG_FILE")
        if env_path:
            config_path = Path(env_path)
        elif DEFAULT_CONFIG_FILE.exists():
            config_path = DEFAULT_CONFIG_FILE
        else:
            logging.getLogger(__name__).warning(
                "No configuration file found (looked for %s relative to %s); using built-in defaults. "
                "Run from the project folder or set QMD_CONFIG_FILE.", DEFAULT_CONFIG_FILE, Path.cwd())
    _yaml_path = config_path
    try:
        settings = Settings(**overrides)
    except ConfigurationError:
        raise
    except Exception as exc:  # pydantic.ValidationError and friends
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc
    finally:
        _yaml_path = None
    return settings


def mm_to_px(mm: float, dpi: float) -> int:
    """Convert millimetres to pixels at the given dpi (rounded)."""
    return int(round(mm * dpi / 25.4))

