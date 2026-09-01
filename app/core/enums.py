from enum import StrEnum


class CameraStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
    MAINTENANCE = "maintenance"


class CameraType(StrEnum):
    FIXED = "fixed"
    PTZ = "ptz"
    DOME = "dome"
    BULLET = "bullet"
    ANPR = "anpr"
    THERMAL = "thermal"
    OTHER = "other"


class CameraTechnology(StrEnum):
    ANALOG = "analog"
    IP = "ip"
    UNKNOWN = "unknown"


class Connectivity(StrEnum):
    FIBER = "fiber"
    FOUR_G = "4g"
    FIVE_G = "5g"
    WIFI = "wifi"
    LAN = "lan"
    UNKNOWN = "unknown"


class OwnershipClass(StrEnum):
    GOVERNMENT = "government"
    PRIVATE = "private"
    PPP = "ppp"


class SiteType(StrEnum):
    TRAFFIC_JUNCTION = "traffic_junction"
    GODOWN = "godown"
    PDS_SHOP = "pds_shop"
    RTO_CHECKPOINT = "rto_checkpoint"
    OFFICE = "office"
    HOSPITAL = "hospital"
    BUS_DEPOT = "bus_depot"
    BORDER_CHECKPOST = "border_checkpost"
    PUBLIC_SPACE = "public_space"
    OTHER = "other"


class StreamProtocol(StrEnum):
    RTSP = "rtsp"
    HLS = "hls"
    WHEP = "whep"
    ONVIF = "onvif"
    SNAPSHOT = "snapshot"


class Reachability(StrEnum):
    PUBLIC_CDN = "public_cdn"
    DIRECT_IP = "direct_ip"
    LAN_ONLY = "lan_only"


class SourceType(StrEnum):
    CSV = "csv"
    MANUAL = "manual"
    API = "api"
    ADAPTER = "adapter"


class LifecycleState(StrEnum):
    ACTIVE = "active"
    DECOMMISSIONED = "decommissioned"


# Which enums a field_mappings value_map may target, and their fallback member.
SOFT_ENUMS: dict[str, tuple[type[StrEnum], StrEnum]] = {
    "status": (CameraStatus, CameraStatus.UNKNOWN),
    "camera_type": (CameraType, CameraType.OTHER),
    "camera_technology": (CameraTechnology, CameraTechnology.UNKNOWN),
    "connectivity": (Connectivity, Connectivity.UNKNOWN),
    "ownership_class": (OwnershipClass, OwnershipClass.GOVERNMENT),
    "site_type": (SiteType, SiteType.OTHER),
}


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    DEPT_ADMIN = "dept_admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class ActorType(StrEnum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"


# Scopes are code-level rather than data, unlike the camera vocabularies: each one
# names a guard that exists in the source. Adding a scope without adding its check
# would be a permission that grants nothing.
#
# Read is statewide, write is department-scoped. An analyst in Rajkot can see
# Surat's cameras but cannot edit them -- the platform exists to remove
# departmental blind spots, so scoping reads would defeat its purpose.
ROLE_SCOPES: dict[Role, frozenset[str]] = {
    Role.SUPER_ADMIN: frozenset(
        {
            "cameras:read", "cameras:write", "cameras:export",
            "coverage:run", "health:write", "streams:credentials", "admin",
        }
    ),
    Role.DEPT_ADMIN: frozenset(
        {"cameras:read", "cameras:write", "cameras:export", "coverage:run", "health:write"}
    ),
    Role.ANALYST: frozenset({"cameras:read", "cameras:export", "coverage:run"}),
    Role.VIEWER: frozenset({"cameras:read"}),
}
