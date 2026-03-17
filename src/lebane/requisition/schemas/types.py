from lebane.core.types import BaseEnum


class RequisitionItemState(BaseEnum):
    DRAFT = "BORRADOR"
    PENDING = "PENDIENTE"
    QUOTED = "COTIZADO"
    REQUESTED = "SOLICITADO"
    PARTIALLY_REQUESTED = "PARCIALMENTE_SOLICITADO"
    PARTIALLY_DELIVERED = "PARCIALMENTE_ENTREGADO"
    DELIVERED = "ENTREGADO"
    CANCELED = "ANULADO"


class UnitOfMeasurementType(BaseEnum):
    UNITS = "UNIDADES"
    PERCENTAGE = "PORCENTAJE"


class UnitOfMeasurement(BaseEnum):
    Units = "Unidades"  # Ew brother eww, what's that brother?
    UNITS = "UNIDADES"
    MG = "MG"
    CG = "CG"
    DG = "DG"
    G = "G"
    GAG = "DAG"
    HG = "HG"
    KG = "KG"
    T = "T"
    MM2 = "MM2"
    CM2 = "CM2"
    DM2 = "DM2"
    M2 = "M2"
    DAM2 = "DAM2"
    HM2 = "HM2"
    KM2 = "KM2"
    M3 = "M3"
    MES = "MES"
    GL = "GL"
    X_LOSA = "X_LOSA"


class ItemType(BaseEnum):
    MATERIAL = "MATERIALES"
    SERVICE = "SERVICIO"
    # LG nuevos q se agregaron en  https://bitbucket.org/lebane-app/lebex/commits/2f231130a9efa10029f94e1a15dc1c9d7479e7fd
    LABOR = "MANO_OBRA"
    MACHINERY = "MAQUINARIA"


class RequisitionCriticality(BaseEnum):
    NORMAL = "NORMAL"
    URGENT = "URGENTE"
    EXTRAORDINARY = "EXTRAORDINARIA"
    THIRD_PARTIES = "TERCEROS"
