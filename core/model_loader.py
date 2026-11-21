"""
3D Model Loader for Atom Character (Phase 1: Static Model Display)

Loads .gltf/.glb models and provides rendering interface for PyQt6.
Supports basic transformations and will integrate with animation machine.
"""

import json
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from core.logger import get_logger

log = get_logger('model_loader')

# Note: Phase 1 loads model structure. For actual rendering, use:
# - PyQt6.Qt3D (if available)
# - PyOpenGL + custom gltf loader
# - Or defer rendering to sprite-based fallback while model loads


@dataclass
class BoneTransform:
    """A bone's transformation matrix"""
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]  # quaternion
    scale: Tuple[float, float, float]
    matrix: Optional[List[float]] = None


@dataclass
class Bone:
    """Skeleton bone with hierarchy"""
    name: str
    index: int
    parent_index: int
    transform: BoneTransform
    children: List['Bone'] = None
    
    def __post_init__(self):
        if self.children is None:
            self.children = []


@dataclass
class AnimationClip:
    """Animation clip with keyframe data"""
    name: str
    duration: float
    looping: bool
    fps: int
    keyframes: Dict[str, List]  # bone_name -> list of keyframes


@dataclass
class ModelMetadata:
    """Model file metadata"""
    path: str
    vertex_count: int
    triangle_count: int
    bone_count: int
    animation_count: int
    texture_path: Optional[str]
    has_materials: bool


class GLTFLoader:
    """
    Loads .gltf or .glb files and extracts metadata.
    
    Phase 1 goal: Validate model structure and confirm hardware can display it.
    No actual rendering yet - just metadata and validation.
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.model_path: Optional[Path] = None
        self.metadata: Optional[ModelMetadata] = None
        self.bones: List[Bone] = []
        self.animations: Dict[str, AnimationClip] = {}
        self.raw_data: Optional[Dict] = None
    
    def load_model(self, model_path: str) -> bool:
        """
        Load a .gltf or .glb model file.
        
        Args:
            model_path: Path to model file
            
        Returns:
            True if loaded successfully, False otherwise
        """
        self.model_path = Path(model_path)
        
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        
        if self.model_path.suffix.lower() == '.glb':
            return self._load_glb()
        elif self.model_path.suffix.lower() == '.gltf':
            return self._load_gltf()
        else:
            raise ValueError(f"Unsupported format: {self.model_path.suffix}")
    
    def _load_gltf(self) -> bool:
        """Load .gltf (text-based JSON + .bin files)"""
        try:
            gltf_data = json.loads(self.model_path.read_text())
            self.raw_data = gltf_data
            self._parse_gltf_metadata(gltf_data)
            return True
        except Exception as e:
            log.error("Error loading .gltf: %s", e)
            return False
    
    def _load_glb(self) -> bool:
        """Load .glb (binary bundled format)"""
        try:
            data = self.model_path.read_bytes()
            # GLB header: magic (4) + version (4) + length (4)
            magic = data[:4]
            if magic != b'glTF':
                raise ValueError("Invalid GLB magic number")
            
            version = struct.unpack('<I', data[4:8])[0]
            length = struct.unpack('<I', data[8:12])[0]
            
            if version != 2:
                raise ValueError(f"Unsupported GLB version: {version}")
            
            # JSON chunk starts at offset 20 (after header + first chunk header)
            chunk_header_offset = 12
            chunk_length = struct.unpack('<I', data[chunk_header_offset:chunk_header_offset+4])[0]
            chunk_type = data[chunk_header_offset+4:chunk_header_offset+8]
            
            if chunk_type != b'JSON':
                raise ValueError("Expected JSON chunk in GLB")
            
            json_offset = chunk_header_offset + 8
            json_str = data[json_offset:json_offset+chunk_length].decode('utf-8')
            gltf_data = json.loads(json_str)
            
            self.raw_data = gltf_data
            self._parse_gltf_metadata(gltf_data)
            return True
            
        except Exception as e:
            log.error("Error loading .glb: %s", e)
            return False
    
    def _parse_gltf_metadata(self, gltf_data: Dict):
        """Extract model metadata from parsed glTF"""
        # Count vertices and triangles
        vertex_count = 0
        triangle_count = 0
        
        meshes = gltf_data.get('meshes', [])
        accessors = gltf_data.get('accessors', [])
        
        for mesh in meshes:
            for primitive in mesh.get('primitives', []):
                indices_accessor_idx = primitive.get('indices')
                if indices_accessor_idx is not None and indices_accessor_idx < len(accessors):
                    accessor = accessors[indices_accessor_idx]
                    triangle_count += accessor.get('count', 0) // 3
                
                position_accessor_idx = primitive.get('attributes', {}).get('POSITION')
                if position_accessor_idx is not None and position_accessor_idx < len(accessors):
                    accessor = accessors[position_accessor_idx]
                    vertex_count = max(vertex_count, accessor.get('count', 0))
        
        # Count bones (nodes with skin)
        bone_count = 0
        skins = gltf_data.get('skins', [])
        for skin in skins:
            joints = skin.get('joints', [])
            bone_count = max(bone_count, len(joints))
        
        # Count animations
        animations = gltf_data.get('animations', [])
        animation_count = len(animations)
        
        # Check for textures
        texture_path = None
        images = gltf_data.get('images', [])
        if images:
            texture_path = images[0].get('uri', 'embedded')
        
        # Check for materials
        has_materials = len(gltf_data.get('materials', [])) > 0
        
        self.metadata = ModelMetadata(
            path=str(self.model_path),
            vertex_count=vertex_count,
            triangle_count=triangle_count,
            bone_count=bone_count,
            animation_count=animation_count,
            texture_path=texture_path,
            has_materials=has_materials
        )
        
        # Parse skeleton hierarchy
        self._parse_skeleton(gltf_data)
        
        # Parse animation clips
        self._parse_animations(gltf_data)
    
    def _parse_skeleton(self, gltf_data: Dict):
        """Extract skeleton hierarchy from glTF"""
        nodes = gltf_data.get('nodes', [])
        skins = gltf_data.get('skins', [])
        
        if not skins:
            return  # No skeleton
        
        skin = skins[0]  # Assume first skin is the main character skeleton
        joints = skin.get('joints', [])
        
        # Build bone hierarchy. Minimal or hand-authored glTF files sometimes
        # reference joints that are not present in nodes; keep metadata usable.
        bone_map = {}
        for bone_idx, node_idx in enumerate(joints):
            node = nodes[node_idx] if 0 <= node_idx < len(nodes) else {"name": f"Bone_{bone_idx}"}
            bone = Bone(
                name=node.get('name', f'Bone_{bone_idx}'),
                index=bone_idx,
                parent_index=-1,
                transform=self._extract_transform(node),
                children=[]
            )
            bone_map[node_idx] = bone

        self.bones = list(bone_map.values())
        
        # Link parent-child relationships
        for bone_idx, node_idx in enumerate(joints):
            if not (0 <= node_idx < len(nodes)):
                continue
            node = nodes[node_idx]
            children = node.get('children', [])
            for child_node_idx in children:
                if child_node_idx in bone_map:
                    child_bone_idx = joints.index(child_node_idx)
                    child_bone = bone_map.get(child_node_idx)
                    if child_bone and bone_idx < len(self.bones):
                        child_bone.parent_index = bone_idx
                        self.bones[bone_idx].children.append(child_bone)
    
    def _extract_transform(self, node: Dict) -> BoneTransform:
        """Extract transformation from glTF node"""
        position = tuple(node.get('translation', [0.0, 0.0, 0.0]))
        rotation = tuple(node.get('rotation', [0.0, 0.0, 0.0, 1.0]))
        scale = tuple(node.get('scale', [1.0, 1.0, 1.0]))
        matrix = node.get('matrix')
        
        return BoneTransform(
            position=position,
            rotation=rotation,
            scale=scale,
            matrix=matrix
        )
    
    def _parse_animations(self, gltf_data: Dict):
        """Extract animation clips from glTF"""
        animations = gltf_data.get('animations', [])
        
        for anim_idx, animation in enumerate(animations):
            name = animation.get('name', f'Animation_{anim_idx}')
            channels = animation.get('channels', [])
            samplers = animation.get('samplers', [])
            
            # Determine animation duration (find max keyframe time)
            max_time = 0.0
            for sampler in samplers:
                input_accessor_idx = sampler.get('input')
                if input_accessor_idx is not None:
                    accessor = gltf_data['accessors'][input_accessor_idx]
                    max_time = max(max_time, accessor.get('max', [0.0])[0])
            
            # Estimate FPS (assume 24 fps if not obvious)
            fps = 24
            
            clip = AnimationClip(
                name=name,
                duration=max_time,
                looping=True,  # Assume looping unless marked otherwise
                fps=fps,
                keyframes={}
            )
            
            self.animations[name] = clip
    
    def validate_model(self) -> Tuple[bool, List[str]]:
        """
        Validate that model meets Atom requirements.
        
        Returns:
            (is_valid, list_of_issues)
        """
        if not self.metadata:
            return False, ["Model not loaded"]
        
        issues = []
        
        # Check vertex count
        if self.metadata.vertex_count > 2000:
            issues.append(f"Too many vertices: {self.metadata.vertex_count} (max 2000)")
        
        # Check bone count
        if self.metadata.bone_count < 3:
            issues.append(f"Not enough bones: {self.metadata.bone_count} (min 12)")
        
        # Check animations
        required_animations = [
            'idle', 'walk', 'run', 'talk', 'listen', 'bored', 'wave', 'spin', 'jump', 'sleep'
        ]
        found_anims = [a.lower() for a in self.animations.keys()]
        missing = [a for a in required_animations if a not in found_anims]
        
        if missing:
            issues.append(f"Missing animations: {', '.join(missing)}")
        
        # Check for materials/textures
        if not self.metadata.has_materials:
            issues.append("Model has no materials")
        
        return len(issues) == 0, issues
    
    def get_animation_names(self) -> List[str]:
        """Get list of available animation names"""
        return list(self.animations.keys())
    
    def get_bone_names(self) -> List[str]:
        """Get list of bone names"""
        return [bone.name for bone in self.bones]
    
    def print_summary(self):
        """Print model summary (useful for debugging)"""
        if not self.metadata:
            log.warning("No model loaded")
            return
        
        log.info("=== Model Summary: %s ===", self.metadata.path)
        log.info("Vertices: %s", self.metadata.vertex_count)
        log.info("Triangles: %s", self.metadata.triangle_count)
        log.info("Bones: %s", self.metadata.bone_count)
        log.info("Animations: %s", self.metadata.animation_count)
        log.info("Textures: %s", self.metadata.texture_path or 'None')
        log.info("Has Materials: %s", self.metadata.has_materials)
        
        log.info("Bones (%s):", len(self.bones))
        for bone in self.bones:
            parent_name = self.bones[bone.parent_index].name if bone.parent_index >= 0 else "Root"
            log.info("  %s (parent: %s)", bone.name, parent_name)
        
        log.info("Animations (%s):", len(self.animations))
        for name, clip in self.animations.items():
            log.info("  %s (%.2fs, %s fps)", name, clip.duration, clip.fps)
        
        is_valid, issues = self.validate_model()
        log.info("Validation: %s", 'PASS' if is_valid else 'FAIL')
        if issues:
            for issue in issues:
                log.warning("  - %s", issue)


def load_atom_model(config: Dict) -> Optional[GLTFLoader]:
    """
    Utility function to load Atom's model from config.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Loaded GLTFLoader instance or None if failed
    """
    model_path = config.get('3d', {}).get('model_path')
    
    if not model_path:
        print("No 3D model path configured")
        return None
    
    loader = GLTFLoader(config)
    
    try:
        success = loader.load_model(model_path)
        if success:
            loader.print_summary()
            return loader
        else:
            print(f"Failed to load model: {model_path}")
            return None
    except Exception as e:
        print(f"Error loading model: {e}")
        return None
