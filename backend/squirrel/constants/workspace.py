
# ——————————————————————————————————————————————————————————————
# Workspace Constants

class WorkspaceStatus:
    CREATED       = "created"
    UPLOADED      = "uploaded"
    PREPROCESSING = "preprocessing"
    MODELING      = "modeling"
    COMPLETED     = "completed"
    FAILED        = "failed"


class DataType:
    STRUCTURED = "structured"
    IMAGE      = "image"
    TEXT       = "text"
    AUDIO      = "audio"

    ALL = {STRUCTURED, IMAGE, TEXT, AUDIO}


class SourceKind:
    UPLOAD    = "upload"
    CONNECTOR = "connector"
