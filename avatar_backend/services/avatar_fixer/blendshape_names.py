"""
ARKit 52 blendshape names and Oculus 15 viseme names.
"""

ARKIT_BLENDSHAPES = [
    "eyeBlinkLeft",
    "eyeLookDownLeft",
    "eyeLookInLeft",
    "eyeLookOutLeft",
    "eyeLookUpLeft",
    "eyeSquintLeft",
    "eyeWideLeft",
    "eyeBlinkRight",
    "eyeLookDownRight",
    "eyeLookInRight",
    "eyeLookOutRight",
    "eyeLookUpRight",
    "eyeSquintRight",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawRight",
    "jawOpen",
    "mouthClose",
    "mouthFunnel",
    "mouthPucker",
    "mouthLeft",
    "mouthRight",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "noseSneerLeft",
    "noseSneerRight",
    "tongueOut",
]

OCULUS_VISEMES = [
    "viseme_sil",
    "viseme_PP",
    "viseme_FF",
    "viseme_TH",
    "viseme_DD",
    "viseme_kk",
    "viseme_CH",
    "viseme_SS",
    "viseme_nn",
    "viseme_RR",
    "viseme_aa",
    "viseme_E",
    "viseme_I",
    "viseme_O",
    "viseme_U",
]

# Extra blendshapes TalkingHead auto-generates from ARKit if missing,
# but including them explicitly ensures maximum compatibility.
TALKINGHEAD_EXTRAS = [
    "mouthOpen",
    "mouthSmile",
    "eyesClosed",
    "eyesLookUp",
    "eyesLookDown",
]

ALL_BLENDSHAPES = ARKIT_BLENDSHAPES + OCULUS_VISEMES + TALKINGHEAD_EXTRAS

# Bone names TalkingHead requires (Mixamo-compatible skeleton)
REQUIRED_BONES = [
    "Hips", "Spine", "Spine1", "Spine2", "Neck", "Head",
    "LeftEye", "RightEye",
    "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
    "RightShoulder", "RightArm", "RightForeArm", "RightHand",
    "LeftUpLeg", "LeftLeg", "LeftFoot",
    "RightUpLeg", "RightLeg", "RightFoot",
]

# Mesh name patterns that should receive morph targets
# (TalkingHead expects targets on all face-related meshes)
FACE_MESH_KEYWORDS = [
    "head", "face", "teeth", "eyeleft", "eyeright",
    "eye_left", "eye_right", "wolf3d_head", "wolf3d_teeth",
]
