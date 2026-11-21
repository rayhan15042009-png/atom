"""
Generate a proper Minecraft-proportioned atom.gltf 3D model with:
- Block-based character matching Minecraft player proportions
- UV coordinates mapped to the standard 64x64 Minecraft skin layout
- Proper per-mesh accessors for each body part
- A second-layer "Hat" mesh so the hair has real volume instead of being
  painted flat on the top of the head cube
- GL_NEAREST filtering for pixel-art textures
- Reference to skin texture PNG files

Output: assets/models/atom.gltf
"""

import json
import struct
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "models" / "atom.gltf"

# Minecraft 64x64 Skin UV Map (from core/skin_loader.py)
SKIN_UV = {
    "head": {
        "front":  (8,  8,  8, 8),  "back":  (24, 8,  8, 8),
        "left":   (0,  8,  8, 8),  "right": (16, 8,  8, 8),
        "top":    (8,  0,  8, 8),  "bottom":(16, 0,  8, 8),
    },
    "body": {
        "front":  (20, 20, 8, 12), "back":  (32, 20, 8, 12),
        "left":   (16, 20, 4, 12), "right": (28, 20, 4, 12),
        "top":    (20, 16, 8, 4),  "bottom":(28, 16, 8, 4),
    },
    "arm_r": {
        "front":  (44, 20, 4, 12), "back":  (52, 20, 4, 12),
        "left":   (40, 20, 4, 12), "right": (48, 20, 4, 12),
        "top":    (44, 16, 4, 4),  "bottom":(48, 16, 4, 4),
    },
    "arm_l": {
        "front":  (36, 52, 4, 12), "back":  (44, 52, 4, 12),
        "left":   (32, 52, 4, 12), "right": (40, 52, 4, 12),
        "top":    (36, 48, 4, 4),  "bottom":(40, 48, 4, 4),
    },
    "leg_r": {
        "front":  (4,  20, 4, 12), "back":  (12, 20, 4, 12),
        "left":   (0,  20, 4, 12), "right": (8,  20, 4, 12),
        "top":    (4,  16, 4, 4),  "bottom":(8,  16, 4, 4),
    },
    "leg_l": {
        "front":  (20, 52, 4, 12), "back":  (28, 52, 4, 12),
        "left":   (16, 52, 4, 12), "right": (24, 52, 4, 12),
        "top":    (20, 48, 4, 4),  "bottom":(24, 48, 4, 4),
    },
    # Second ("hat") skin layer — hair volume. Rendered as a slightly larger
    # cube over the head; texels left transparent in the skin fall through to
    # the head beneath (the renderer cuts them with an alpha test).
    "hat": {
        "front":  (40, 8,  8, 8),  "back":  (56, 8,  8, 8),
        "left":   (48, 8,  8, 8),  "right": (32, 8,  8, 8),
        "top":    (40, 0,  8, 8),  "bottom":(48, 0,  8, 8),
    },
}

# Block dimensions in world units (Minecraft proportions)
BLOCK_DIMS = {
    "head": (0.50, 0.50, 0.50),
    "body": (0.50, 0.60, 0.25),
    "arm":  (0.25, 0.60, 0.25),
    "leg":  (0.25, 0.60, 0.25),
    # Minecraft's hat layer is 8.5/8 of the head box.
    "hat":  (0.54, 0.54, 0.54),
}

TEX_SIZE = 64  # Minecraft skin texture size


def uv_to_texcoord(x, y, w, h, flip_v=True):
    """Convert skin pixel coords to glTF UV coordinates (OpenGL convention: 0 at bottom)."""
    u_min = x / TEX_SIZE
    u_max = (x + w) / TEX_SIZE
    if flip_v:
        v_min = (TEX_SIZE - (y + h)) / TEX_SIZE
        v_max = (TEX_SIZE - y) / TEX_SIZE
    else:
        v_min = y / TEX_SIZE
        v_max = (y + h) / TEX_SIZE
    return (u_min, v_min, u_max, v_max)


def build_cube_vertices(w, h, d, uv_rects):
    """Build vertex data for a cube with per-face UV coordinates.
    
    Returns (positions, texcoords, normals, indices)
    - 24 vertices (4 per face x 6 faces)
    - 36 indices (2 triangles x 3 verts x 6 faces)
    """
    # Face definitions with CCW winding
    faces = [
        ([(1,0,1), (1,1,1), (0,1,1), (0,0,1)], (0, 0, 1), "front"),
        ([(0,0,0), (0,1,0), (1,1,0), (1,0,0)], (0, 0, -1), "back"),
        ([(0,1,1), (1,1,1), (1,1,0), (0,1,0)], (0, 1, 0), "top"),
        ([(0,0,0), (1,0,0), (1,0,1), (0,0,1)], (0, -1, 0), "bottom"),
        ([(1,0,0), (1,1,0), (1,1,1), (1,0,1)], (1, 0, 0), "right"),
        ([(0,0,1), (0,1,1), (0,1,0), (0,0,0)], (-1, 0, 0), "left"),
    ]
    
    positions, texcoords, normals = [], [], []
    uv_map = {"front": 0, "back": 1, "top": 2, "bottom": 3, "right": 4, "left": 5}
    
    for vert_offsets, normal, face_name in faces:
        u_min, v_min, u_max, v_max = uv_rects[face_name]
        
        for i, (ox, oy, oz) in enumerate(vert_offsets):
            x = (ox - 0.5) * w
            y = (oy - 0.5) * h
            z = (oz - 0.5) * d
            positions.append((x, y, z))
            normals.append(normal)
            
            if i == 0:
                texcoords.append((u_min, v_min))
            elif i == 1:
                texcoords.append((u_min, v_max))
            elif i == 2:
                texcoords.append((u_max, v_max))
            elif i == 3:
                texcoords.append((u_max, v_min))
    
    indices = []
    for face_idx in range(6):
        base = face_idx * 4
        indices.extend([base, base+1, base+2, base, base+2, base+3])
    
    return positions, texcoords, normals, indices


def generate_gltf():
    """Generate the complete glTF model with proper per-mesh accessors."""
    
    # Mesh indices 0-5 are load-bearing: ui/renderer_3d.py draws them by
    # index. New parts must be appended, never inserted.
    body_parts = [
        ("Head",    BLOCK_DIMS["head"], SKIN_UV["head"],    [0.0, 0.55, 0.0]),
        ("Body",    BLOCK_DIMS["body"], SKIN_UV["body"],    [0.0, 0.0, 0.0]),
        ("LeftArm",  BLOCK_DIMS["arm"],  SKIN_UV["arm_l"],  [-0.375, 0.0, 0.0]),
        ("RightArm", BLOCK_DIMS["arm"],  SKIN_UV["arm_r"],  [0.375, 0.0, 0.0]),
        ("LeftLeg",  BLOCK_DIMS["leg"],  SKIN_UV["leg_l"],  [-0.125, -0.45, 0.0]),
        ("RightLeg", BLOCK_DIMS["leg"],  SKIN_UV["leg_r"],  [0.125, -0.45, 0.0]),
        ("Hat",     BLOCK_DIMS["hat"],  SKIN_UV["hat"],    [0.0, 0.55, 0.0]),
    ]
    
    # Build vertex data for each body part
    mesh_geo = []
    for name, dims, uv_map, translation in body_parts:
        w, h, d = dims
        uv_rects = {}
        for face_name, (x, y, fw, fh) in uv_map.items():
            uv_rects[face_name] = uv_to_texcoord(x, y, fw, fh)
        positions, texcoords, normals, indices = build_cube_vertices(w, h, d, uv_rects)
        mesh_geo.append({
            "name": name,
            "positions": positions,
            "texcoords": texcoords,
            "normals": normals,
            "indices": indices,
            "translation": translation,
        })
    
    # Compute offsets for combined buffer
    # Layout: interleaved [POSITION (3f), NORMAL (3f), TEXCOORD (2f)] per vertex
    vertex_stride = 32  # 8 floats x 4 bytes
    
    vertex_offset = 0
    index_offset = 0
    mesh_offsets = []
    
    for mg in mesh_geo:
        num_verts = len(mg["positions"])
        num_idx = len(mg["indices"])
        mesh_offsets.append({
            "vertex_start": vertex_offset,
            "vertex_count": num_verts,
            "index_start": index_offset,
            "index_count": num_idx,
        })
        vertex_offset += num_verts
        index_offset += num_idx
    
    # Build interleaved vertex buffer
    interleaved = bytearray()
    for mg in mesh_geo:
        for i in range(len(mg["positions"])):
            px, py, pz = mg["positions"][i]
            nx, ny, nz = mg["normals"][i]
            u, v = mg["texcoords"][i]
            interleaved.extend(struct.pack('<ffffffff', px, py, pz, nx, ny, nz, u, v))
    
    # Build index buffer (unsigned short)
    index_buf = bytearray()
    for mg in mesh_geo:
        base_vertex = 0  # indices are per-mesh (relative to 0)
        for idx in mg["indices"]:
            index_buf.extend(struct.pack('<H', idx))
    
    interleaved_size = len(interleaved)
    index_size = len(index_buf)
    
    # Build base64 buffer
    import base64
    combined = interleaved + index_buf
    buffer_base64 = base64.b64encode(combined).decode('ascii')
    
    # ── Buffer views ──
    # View 0: all interleaved vertex data (stride=32)
    # View 1: all index data
    buffer_views = [
        {"buffer": 0, "byteOffset": 0, "byteLength": interleaved_size, "byteStride": 32, "target": 34962},
        {"buffer": 0, "byteOffset": interleaved_size, "byteLength": index_size, "target": 34963},
    ]
    
    # ── Per-mesh accessors ──
    # Each mesh gets 4 accessors: POSITION, NORMAL, TEXCOORD_0, INDICES
    # Total: len(body_parts) x 4 accessors
    accessors = []
    meshes = []
    acc_idx = 0
    
    for i, mg in enumerate(mesh_geo):
        mo = mesh_offsets[i]
        name = mg["name"]
        
        # Compute bounds
        xs = [p[0] for p in mg["positions"]]
        ys = [p[1] for p in mg["positions"]]
        zs = [p[2] for p in mg["positions"]]
        
        # POSITION accessor (within buffer view 0, offset = vertex_start * stride)
        accessors.append({
            "bufferView": 0,
            "byteOffset": mo["vertex_start"] * 32,
            "componentType": 5126,
            "count": mo["vertex_count"],
            "type": "VEC3",
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        })
        pos_acc = acc_idx
        acc_idx += 1
        
        # NORMAL accessor (offset = vertex_start * 32 + 12)
        accessors.append({
            "bufferView": 0,
            "byteOffset": mo["vertex_start"] * 32 + 12,
            "componentType": 5126,
            "count": mo["vertex_count"],
            "type": "VEC3",
        })
        nrm_acc = acc_idx
        acc_idx += 1
        
        # TEXCOORD accessor (offset = vertex_start * 32 + 24)
        accessors.append({
            "bufferView": 0,
            "byteOffset": mo["vertex_start"] * 32 + 24,
            "componentType": 5126,
            "count": mo["vertex_count"],
            "type": "VEC2",
        })
        tex_acc = acc_idx
        acc_idx += 1
        
        # INDICES accessor (within buffer view 1, offset = index_start * 2, since USHORT = 2 bytes)
        accessors.append({
            "bufferView": 1,
            "byteOffset": mo["index_start"] * 2,
            "componentType": 5123,
            "count": mo["index_count"],
            "type": "SCALAR",
            "min": [0],
            "max": [mo["vertex_count"] - 1],
        })
        idx_acc = acc_idx
        acc_idx += 1
        
        meshes.append({
            "name": name,
            "primitives": [{
                "attributes": {
                    "POSITION": pos_acc,
                    "NORMAL": nrm_acc,
                    "TEXCOORD_0": tex_acc,
                },
                "indices": idx_acc,
                "material": 0,
            }]
        })
    
    # ── Nodes ──
    node_indices = {
        name: i + 1 for i, (name, _, _, _) in enumerate(body_parts)
    }
    child_lists = {
        "Body": [node_indices["Head"], node_indices["LeftArm"], node_indices["RightArm"],
                 node_indices["LeftLeg"], node_indices["RightLeg"]],
    }
    
    nodes = [
        {"name": "Root", "children": [1]},  # Root -> Body
        {"name": "Body", "translation": [0.0, 0.75, 0.0], "children": child_lists["Body"], "mesh": 1},  # Body mesh
    ]
    
    # Add remaining body part nodes
    for i, (name, _, _, translation) in enumerate(body_parts):
        if name == "Body":
            continue
        nodes.append({
            "name": name,
            "translation": translation,
            "mesh": i if name != "Body" else 1,
        })
    
    # Fix mesh indices for nodes (mesh index = body_parts index)
    nodes[0] = {"name": "Root", "children": [1]}  # Root
    nodes[1] = {"name": "Body", "translation": [0.0, 0.75, 0.0], "children": child_lists["Body"], "mesh": 1}
    for i in range(2, len(body_parts) + 1):
        nodes[i]["mesh"] = i - 1  # mesh index matches node order
    
    # Actually, mesh indices are 0-5 for the 6 body parts
    # Fix node mesh mapping properly
    mesh_name_to_idx = {mg["name"]: i for i, mg in enumerate(mesh_geo)}
    for node in nodes:
        name = node.get("name", "")
        if name in mesh_name_to_idx:
            node["mesh"] = mesh_name_to_idx[name]
    
    # ── Materials, Textures, Images ──
    materials = [{
        "name": "AtomSkin",
        "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0, "texCoord": 0},
            "metallicFactor": 0.0,
            "roughnessFactor": 0.8,
        },
        "alphaMode": "BLEND",
    }]
    
    textures = [{"sampler": 0, "source": 0}]
    
    images = [{"uri": "../skins/atom_skin.png"}]  # Relative to assets/models/ -> assets/skins/
    
    samplers = [{
        "magFilter": 9728,   # GL_NEAREST
        "minFilter": 9728,   # GL_NEAREST
        "wrapS": 33071,      # GL_CLAMP_TO_EDGE
        "wrapT": 33071,
    }]
    
    # ── Buffer (base64 embedded) ──
    buffers = [{
        "uri": f"data:application/octet-stream;base64,{buffer_base64}",
        "byteLength": len(combined),
    }]
    
    # ── Scene ──
    scenes = [{"name": "Atom", "nodes": [0]}]
    
    # Build final glTF
    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "Atom glTF Model Generator v2",
            "copyright": "Atom Roamer - Minecraft character with skin UV mapping",
        },
        "scene": 0,
        "scenes": scenes,
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": buffers,
        "materials": materials,
        "textures": textures,
        "images": images,
        "samplers": samplers,
    }
    
    return gltf


def main():
    gltf = generate_gltf()
    
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(gltf, f, indent=2, ensure_ascii=False)
    
    # Verify
    with open(OUTPUT, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    
    num_meshes = len(loaded['meshes'])
    num_accessors = len(loaded['accessors'])
    num_nodes = len(loaded['nodes'])
    buf_size = loaded['buffers'][0]['byteLength']
    
    print(f"Generated {OUTPUT}")
    print(f" Nodes: {num_nodes}")
    print(f" Meshes: {num_meshes}")
    print(f" Accessors: {num_accessors} ({num_meshes} x 4 = {num_meshes * 4})")
    print(f" Vertex bytes: {num_meshes * 24 * 32} (24 verts x 32-byte stride)")
    print(f" Buffer: {buf_size} bytes")
    
    # Print mesh names and their accessor indices
    for i, mesh in enumerate(loaded['meshes']):
        prim = mesh['primitives'][0]
        pos = prim['attributes']['POSITION']
        idx = prim['indices']
        acc = loaded['accessors'][pos]
        print(f" Mesh {i} '{mesh['name']}': {acc['count']} vertices, indices={loaded['accessors'][idx]['count']}")


if __name__ == "__main__":
    main()
