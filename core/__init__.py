"""Core modules for Atom character behavior and animation"""

from core.model_loader import GLTFLoader, load_atom_model
from core.installer import DependencyInstaller

# AnimationMachine, AnimationState, Emotion moved to ui.animation_machine (Step 5)

__all__ = [
    'GLTFLoader',
    'load_atom_model',
    'DependencyInstaller',
]
