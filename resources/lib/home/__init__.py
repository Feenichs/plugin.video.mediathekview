# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT


def open_home_window():
    """ Open the TV interface, run as script: RunScript(plugin.video.mediathekview,home) """
    import xbmcaddon
    import resources.lib.appContext as appContext
    from resources.lib.loggerKodi import LoggerKodi
    from resources.lib.settingsKodi import SettingsKodi
    from resources.lib.notifierKodi import NotifierKodi

    appContext.init()
    appContext.initAddon(xbmcaddon.Addon())
    appContext.initLogger(LoggerKodi(appContext.ADDONCLASS.getAddonInfo('id'), appContext.ADDONCLASS.getAddonInfo('version')))
    appContext.initSettings(SettingsKodi(appContext.ADDONCLASS))
    appContext.initNotifier(NotifierKodi(appContext.ADDONCLASS))

    # imported here as the window requires a running Kodi GUI
    from resources.lib.home.window import open_home_window as _open_home_window
    _open_home_window()
