"""Core download queue: byte-progress downloads with admin history, grouped
Hugging Face repo jobs, and provider-authenticated fetches."""

from src.features.downloads.manager import DownloadManager
from src.features.downloads.models import Download, DownloadSettings, DownloadStatus, DownloadType
from src.features.downloads.repository import DownloadRepository
from src.features.downloads.worker import DownloadWorker
from src.features.downloads.exceptions import (
    DownloadException,
    DownloadNotFoundException,
    DownloadOperationException,
    DownloadQueueException,
    InvalidStatusException,
    InvalidTypeException,
)

__all__ = [
    "Download",
    "DownloadException",
    "DownloadManager",
    "DownloadNotFoundException",
    "DownloadOperationException",
    "DownloadQueueException",
    "DownloadRepository",
    "DownloadSettings",
    "DownloadStatus",
    "DownloadType",
    "DownloadWorker",
    "InvalidStatusException",
    "InvalidTypeException",
]
