"""QGIS Doctor — plugin entry point."""


def classFactory(iface):
    from .plugin_main import QGISDoctorPlugin
    return QGISDoctorPlugin(iface)
