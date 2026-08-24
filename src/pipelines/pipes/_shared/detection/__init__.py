"""Shared detection framework used across detailer pipe variants."""
from .base_detector import BaseDetector
from .face_detector import FaceDetector
from .hand_detector import HandDetector
from .eye_detector import EyeDetector
from .teeth_detector import TeethDetector
from .person_detector import PersonDetector

__all__ = [
    'BaseDetector',
    'FaceDetector',
    'HandDetector',
    'EyeDetector',
    'TeethDetector',
    'PersonDetector'
]
