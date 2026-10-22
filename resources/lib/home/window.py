# -*- coding: utf-8 -*-
"""
TV interface in the style of the media library apps on Fire TV: a tab bar,
rows of tiles with a large preview of the focused film, grid pages and
detail pages with more episodes of the show, made for remote controls.

SPDX-License-Identifier: MIT
"""
import os
import threading
import time

try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

import xbmc
import xbmcgui

import resources.lib.appContext as appContext
from resources.lib.home import data

ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4
ACTION_PREVIOUS_MENU = 10
ACTION_SHOW_INFO = 11
ACTION_NAV_BACK = 92
ACTION_CONTEXT_MENU = 117
ACTION_MOUSE_LONG_CLICK = 108

NAV_LIST = 100
ROWS_GROUPLIST = 300
NUM_ROWS = 10
ROW_LISTS = tuple(range(301, 301 + NUM_ROWS))
ROW_LABELS = tuple(range(401, 401 + NUM_ROWS))
ROW_GROUPS = tuple(range(501, 501 + NUM_ROWS))
GRID_PANEL = 600
GRID_COLUMNS = 5
BUTTON_LIST = 700
EPISODE_LIST = 720
DETAIL_LISTS = (BUTTON_LIST, EPISODE_LIST)

CACHE_SECONDS = 300
GRID_PAGE_SIZE = 30
TAB_SWITCH_DELAY = 0.35
PLAYBACK_END_DELAY = 2
WINDOW_PROPERTY = 'mediathekview_home_window'

TAB_HOME = 'home'
TAB_LIVE = 'live'
TAB_CHANNELS = 'channels'
TAB_LETTERS = 'letters'
TAB_SEARCH = 'search'
TAB_MORE = 'more'


def _string(string_id):
    return appContext.ADDONCLASS.getLocalizedString(string_id)


def _log(message, *args):
    appContext.MVLOGGER.get_new_logger('Home').debug(message, *args)


def _format_time(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)
    return '%d:%02d' % (seconds // 60, seconds % 60)


class HomeWindow(xbmcgui.WindowXML):

    def __init__(self, *args, **kwargs):
        super(HomeWindow, self).__init__(*args, **kwargs)
        self._lock = threading.RLock()
        self._generation = 0
        self._cache = {}
        self._tabs = []
        self._current_tab = None
        self._tab_timer = None
        # views opened on top of the tab: rows of a channel, grids and detail pages
        self._stack = []
        self._row_data = {}
        self._row_offset = 0
        self._grid = None
        self._detail = None
        self._buttons = []
        self._tab_focus = None
        self._restore = None
        self._focus_rows = False
        self._initialised = False
        self._closing = False
        self._refresh_pending = False
        self._window_id = None
        self._player = _Player(self._on_playback_end)

    # Kodi callbacks

    def onInit(self):
        # the automatic database update waits for user activity
        appContext.MVSETTINGS.user_activity()
        self._window_id = xbmcgui.getCurrentWindowId()
        xbmcgui.Window(10000).setProperty(WINDOW_PROPERTY, str(self._window_id))
        if self._initialised:
            # back from playback or another window
            if xbmc.getCondVisibility('Player.HasMedia'):
                self._refresh_pending = True
            else:
                self._start(self._refresh_view)
            return
        self._initialised = True
        self.setProperty('view', 'tab')
        self._load_root()

    def onAction(self, action):
        action_id = action.getId()
        focus_id = self.getFocusId()

        if action_id in (ACTION_NAV_BACK, ACTION_PREVIOUS_MENU):
            self._on_back(focus_id)
            return

        if action_id in (ACTION_CONTEXT_MENU, ACTION_MOUSE_LONG_CLICK):
            item = self._focused_item(focus_id)
            if item:
                self._context_menu(item)
            return

        if action_id == ACTION_SHOW_INFO:
            item = self._focused_item(focus_id)
            if item and item['plot']:
                xbmcgui.Dialog().textviewer(item['label'], item['plot'])
            return

        if focus_id == NAV_LIST and action_id in (ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT):
            self._schedule_tab_switch()
            return

        if focus_id == NAV_LIST and action_id == ACTION_MOVE_DOWN:
            self._focus_content()
            return

        if focus_id in ROW_LISTS and action_id in (ACTION_MOVE_UP, ACTION_MOVE_DOWN):
            self._move_row(ROW_LISTS.index(focus_id), up=action_id == ACTION_MOVE_UP)
            return

        if focus_id in DETAIL_LISTS and action_id in (ACTION_MOVE_UP, ACTION_MOVE_DOWN):
            self._move_detail(focus_id, up=action_id == ACTION_MOVE_UP)
            return

        if focus_id == GRID_PANEL and action_id == ACTION_MOVE_UP:
            # the panel moves up by itself, only leave it from the first line
            if self.getControl(GRID_PANEL).getSelectedPosition() < GRID_COLUMNS:
                self.setFocusId(NAV_LIST)

    def onClick(self, control_id):
        if control_id == NAV_LIST:
            self._switch_tab(self._selected_tab(), focus_rows=True)
            return
        if control_id == BUTTON_LIST:
            self._on_button()
            return
        item = self._focused_item(control_id)
        if item:
            self._activate(item, from_episodes=control_id == EPISODE_LIST)

    # Navigation

    def _on_back(self, focus_id):
        if self._stack:
            self._close_view()
            return
        if focus_id != NAV_LIST:
            self._set_row_offset(0)
            self.setFocusId(NAV_LIST)
            return
        self.close_window()

    def close_window(self):
        self._closing = True
        with self._lock:
            self._generation += 1
        if self._tab_timer:
            self._tab_timer.cancel()
        self.close()

    def _focus_content(self):
        view = self.getProperty('view')
        if view == 'grid':
            if self._grid and self._grid['items']:
                self.setFocusId(GRID_PANEL)
            return
        if view == 'detail':
            self.setFocusId(BUTTON_LIST)
            return
        rows = [row for row in self._filled_rows() if row >= self._row_offset]
        if rows:
            self.setFocusId(ROW_LISTS[rows[0]])
        else:
            self._focus_rows = True

    def _filled_rows(self):
        return sorted(ROW_LISTS.index(list_id) for list_id in self._row_data)

    def _set_row_offset(self, offset):
        """ Show the filled rows from offset on, so that the focused row is the topmost one """
        self._row_offset = offset
        for index in self._filled_rows():
            self.getControl(ROW_GROUPS[index]).setVisible(index >= offset)

    def _move_row(self, index, up):
        filled = self._filled_rows()
        if up:
            above = [row for row in filled if row < index]
            if not above:
                self._set_row_offset(0)
                self.setFocusId(NAV_LIST)
                return
            target = above[-1]
        else:
            below = [row for row in filled if row > index]
            if not below:
                return
            target = below[0]
        self._set_row_offset(target)
        self.setFocusId(ROW_LISTS[target])

    def _move_detail(self, focus_id, up):
        lists = [BUTTON_LIST]
        if self._detail and self._detail.get('episodes'):
            lists.append(EPISODE_LIST)
        index = lists.index(focus_id) if focus_id in lists else 0
        if up:
            if index == 0:
                self.setFocusId(NAV_LIST)
                return
            target = lists[index - 1]
        else:
            if index + 1 >= len(lists):
                return
            target = lists[index + 1]
        self.setFocusId(target)

    # Tabs

    def _load_root(self):
        self._tabs = [
            {'id': TAB_HOME, 'title': _string(30610)},
            {'id': TAB_LIVE, 'title': _string(30611)},
            {'id': TAB_CHANNELS, 'title': _string(30612)},
            {'id': TAB_LETTERS, 'title': _string(30613)},
            {'id': TAB_SEARCH, 'title': _string(30614)},
            {'id': TAB_MORE, 'title': _string(30615)},
        ]
        nav = self.getControl(NAV_LIST)
        nav.reset()
        for tab in self._tabs:
            list_item = xbmcgui.ListItem(label=tab['title'], offscreen=True)
            list_item.setProperty('tab', tab['id'])
            nav.addItem(list_item)
        self._switch_tab(TAB_HOME, force=True)
        self.setFocusId(NAV_LIST)

    def _selected_tab(self):
        position = self.getControl(NAV_LIST).getSelectedPosition()
        if 0 <= position < len(self._tabs):
            return self._tabs[position]['id']
        return self._tabs[0]['id'] if self._tabs else None

    def _schedule_tab_switch(self):
        if self._tab_timer:
            self._tab_timer.cancel()
        timer = threading.Timer(TAB_SWITCH_DELAY, lambda: self._switch_tab(self._selected_tab()))
        timer.daemon = True
        self._tab_timer = timer
        timer.start()

    def _switch_tab(self, tab_id, focus_rows=False, force=False):
        if self._closing or tab_id is None:
            return
        if self._stack:
            self._stack = []
            self._grid = self._detail = None
            self.setProperty('view', 'tab')
            self.setProperty('page_title', '')
            force = True
        if tab_id != self._current_tab or force:
            if tab_id != self._current_tab:
                self._restore = None
            self._current_tab = tab_id
            nav = self.getControl(NAV_LIST)
            for position, tab in enumerate(self._tabs):
                nav.getListItem(position).setProperty('active', 'true' if tab['id'] == tab_id else '')
            self._show_rows(lambda: self._tab_rows(tab_id))
        if focus_rows:
            self._focus_content()

    def _tab_rows(self, tab_id):
        if tab_id == TAB_HOME:
            return self._home_rows()
        if tab_id == TAB_LIVE:
            return self._live_rows()
        if tab_id == TAB_CHANNELS:
            return self._channel_list_rows()
        if tab_id == TAB_LETTERS:
            return self._letter_rows()
        if tab_id == TAB_SEARCH:
            return self._search_rows()
        if tab_id == TAB_MORE:
            return self._more_rows()
        return []

    def _cached(self, key, load):
        """ Results of the database are kept for a while, the database changes once or twice a day """
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        with data.Database() as db:
            value = load(db)
        self._cache[key] = (now, value)
        return value

    def _home_rows(self):
        def _load(db):
            rows = []
            rows.append({'title': _string(30621), 'items': data.recent_films(db, exclude=data.FOREIGN_CHANNELS),
                         'more': lambda db: data.recent_films(db, exclude=data.FOREIGN_CHANNELS, limit=data.SEARCH_MAX_ITEMS)})
            live = data.livestreams(db)
            if live:
                rows.append({'title': _string(30622), 'items': [i for row in data.live_rows(live)[:1] for i in row[1]]})
            for string_id, conditions, min_duration, days in data.GENRE_ROWS:
                rows.append({'title': _string(string_id), 'items': data.genre_films(db, conditions, min_duration, days),
                             'more': lambda db, c=conditions, m=min_duration, d=days: data.genre_films(db, c, m, d, limit=data.SEARCH_MAX_ITEMS)})
            rows.append({'title': _string(30629), 'items': data.new_shows(db)})
            for channel in ('ARD', 'ZDF', 'ARTE.DE', '3Sat'):
                name = data.channel_info(channel)[0]
                rows.append({'title': _string(30626) % name, 'items': data.recent_films(db, [channel], days=3),
                             'more': lambda db, c=channel: data.recent_films(db, [c], days=7, limit=data.SEARCH_MAX_ITEMS),
                             'channel': channel})
            return rows

        if not self._database_ready():
            return []
        rows = self._cached(TAB_HOME, _load)
        with data.Database() as db:
            resume = data.continue_watching(db)
        if resume:
            rows = [{'title': _string(30620), 'items': resume}] + rows
        return self._with_history(rows)

    def _live_rows(self):
        if not self._database_ready():
            return []
        live = self._cached(TAB_LIVE, data.livestreams)
        return [{'title': _string(string_id), 'items': items} for string_id, items in data.live_rows(live) if items]

    def _channel_list_rows(self):
        if not self._database_ready():
            return []
        items = self._cached(TAB_CHANNELS, data.channels)
        german = [i for i in items if i['channel'] not in data.FOREIGN_CHANNELS + ('ORF', 'SRF', 'SRF.Podcast')]
        others = [i for i in items if i not in german]
        rows = [{'title': _string(30612), 'items': german}]
        if others:
            rows.append({'title': _string(30633), 'items': others})
        return rows

    def _letter_rows(self):
        if not self._database_ready():
            return []
        rows = [{'title': _string(30634), 'items': self._cached(TAB_LETTERS, data.letters)}]
        new = self._cached('new_shows', lambda db: data.new_shows(db, days=2, limit=data.ROW_MAX_ITEMS))
        if new:
            rows.append({'title': _string(30629), 'items': new})
        return rows

    def _search_rows(self):
        rows = [{'title': _string(30614), 'items': [data.make_item(data.KIND_ACTION, _string(30640), action='search')]}]
        history = data.load_searches()
        if history:
            rows.append({'title': _string(30641), 'items': [
                data.make_item(data.KIND_ACTION, query, action='search_query') for query in history]})
        saved = data.saved_searches()
        if saved:
            rows.append({'title': _string(30642), 'items': [
                data.make_item(data.KIND_ACTION, entry.get('name') or '?', action='saved_search', entry=entry) for entry in saved]})
        return rows

    def _more_rows(self):
        items = [
            data.make_item(data.KIND_ACTION, _string(30650), action='settings'),
            data.make_item(data.KIND_ACTION, _string(30651), action='classic'),
            data.make_item(data.KIND_ACTION, _string(30652), action='dbinfo'),
        ]
        if appContext.MVSETTINGS.getDatabaseUpateMode() in (1, 2):
            items.append(data.make_item(data.KIND_ACTION, _string(30653), action='dbupdate'))
        items.append(data.make_item(data.KIND_ACTION, _string(30654), action='refresh'))
        return [{'title': _string(30615), 'items': items}]

    def _database_ready(self):
        """ Shows a hint while the service downloads the database for the first time """
        try:
            with data.Database() as db:
                status, _ = db.status()
                ready = status in ('IDLE', 'UPDATING') and db.execute('SELECT 1 FROM film LIMIT 1')
        # pylint: disable=broad-except
        except Exception:
            ready = False
        self.setProperty('empty_text', '' if ready else _string(30682))
        return bool(ready)

    @staticmethod
    def _with_history(rows):
        history = data.load_history()
        for row in rows:
            data.apply_history(row['items'], history)
        return rows

    # Rows

    def _show_rows(self, get_rows):
        with self._lock:
            self._generation += 1
            generation = self._generation
        self._row_data = {}
        self._row_offset = 0
        for index, group_id in enumerate(ROW_GROUPS):
            self.getControl(ROW_LISTS[index]).reset()
            self.getControl(group_id).setVisible(False)
        self.setProperty('empty', '')
        self.setProperty('empty_text', '')
        self.setProperty('loading', 'true')

        def _run():
            rows = get_rows()[:len(ROW_LISTS)]
            if self._stale(generation):
                return
            shown = 0
            for row in rows:
                items = list(row['items'][:data.ROW_MAX_ITEMS])
                if not items:
                    continue
                # filled rows without gaps: the preview of the tab bar shows the first tile of the first row
                index = shown
                if row.get('more') and len(row['items']) >= data.ROW_MAX_ITEMS:
                    items.append(data.make_item(data.KIND_ALL, _string(30671), row_title=row['title'], more=row['more']))
                with self._lock:
                    if self._stale(generation):
                        return
                    self._fill_row(index, row['title'], items)
                shown += 1
                if self._focus_rows:
                    self._focus_rows = False
                    self._focus_content()
            if not self._stale(generation):
                self.setProperty('loading', '')
                if not shown:
                    self.setProperty('empty', 'true')
                    if self.getFocusId() in ROW_LISTS:
                        self.setFocusId(NAV_LIST)

        self._start(_run)

    def _fill_row(self, index, title, items):
        list_id = ROW_LISTS[index]
        control = self.getControl(list_id)
        control.reset()
        control.addItems([self._make_listitem(item) for item in items])
        self.getControl(ROW_LABELS[index]).setLabel(title or '')
        self._row_data[list_id] = items
        self.getControl(ROW_GROUPS[index]).setVisible(index >= self._row_offset)
        self._restore_focus(list_id, control, len(items))

    # Views on top of the tabs

    def _push(self, view):
        """ Open a view, remembering the focused tile to return to it """
        focus_id = self.getFocusId()
        position = None
        if focus_id in ROW_LISTS + (GRID_PANEL,) + DETAIL_LISTS:
            position = (focus_id, self.getControl(focus_id).getSelectedPosition(), self._row_offset)
        if self._stack:
            self._stack[-1]['focus'] = position
        else:
            self._tab_focus = position
        self._stack.append(view)
        self._render(view)

    def _close_view(self):
        self._stack.pop()
        if self._stack:
            view = self._stack[-1]
            self._restore = view.get('focus')
            self._render(view)
            return
        self._grid = self._detail = None
        self.setProperty('view', 'tab')
        self.setProperty('page_title', '')
        self._restore = self._tab_focus
        self._focus_rows = not self._restore
        self._show_rows(lambda: self._tab_rows(self._current_tab))

    def _render(self, view):
        kind = view['type']
        if kind == 'rows':
            self._grid = self._detail = None
            self.setProperty('view', 'tab')
            self.setProperty('page_title', view['title'])
            self._focus_rows = not self._restore
            self._show_rows(view['rows'])
        elif kind == 'grid':
            self._detail = None
            self._show_grid(view)
        elif kind == 'detail':
            self._grid = None
            self._show_detail(view)

    def _open_grid(self, title, load, page=1, source=None):
        self._push({'type': 'grid', 'title': title, 'source': source or {'load': load, 'items': None}, 'page': page})

    def _open_channel(self, item):
        channel = item['channel']

        def _rows():
            def _load(db):
                return [
                    {'title': _string(30635), 'items': data.recent_films(db, [channel], days=7, home=False),
                     'more': lambda db: data.recent_films(db, [channel], days=30, limit=data.SEARCH_MAX_ITEMS, home=False)},
                    # search and the start letters of the shows of the channel
                    {'title': _string(30646), 'items': [data.make_item(data.KIND_ACTION, _string(30645) % item['label'],
                                                                       action='search_channel', channel=channel)]
                     + data.letters(db, channel)},
                    {'title': _string(30629), 'items': data.channel_shows(db, channel, recent_days=14, limit=data.ROW_MAX_ITEMS),
                     'more': lambda db: data.channel_shows(db, channel, recent_days=30)},
                    {'title': _string(30628), 'items': data.channel_shows(db, channel)[:data.ROW_MAX_ITEMS],
                     'more': lambda db: data.channel_shows(db, channel)},
                ]
            return self._with_history(self._cached('channel:' + channel, _load))

        self._push({'type': 'rows', 'title': item['label'], 'rows': _rows})

    def _open_show(self, item):
        showids, channels = item['showids'], item['channels']
        self._open_grid(item['show'] or item['label'],
                        lambda db: data.show_films(db, showids, channels if len(showids) == 1 else None))

    def _open_letter(self, item):
        letter, channel = item['letter'], item['channel']
        title = '%s  ·  %s' % (data.channel_info(channel)[0], item['label']) if channel else item['label']
        self._open_grid(title, lambda db: data.letter_shows(db, letter, channel or None))

    def _open_all(self, item):
        self._open_grid(item['row_title'] or _string(30671), item['more'])

    # Grid

    def _show_grid(self, view):
        with self._lock:
            self._generation += 1
            generation = self._generation
        source = view['source']
        page = view['page']
        panel = self.getControl(GRID_PANEL)
        title = view['title'] if page == 1 else '%s  ·  %s' % (view['title'], _string(30672) % page)
        self.setProperty('view', 'grid')
        self.setProperty('page_title', title)
        self.setProperty('empty', '')
        self.setProperty('empty_text', '')
        self.setProperty('loading', 'true')
        panel.reset()
        self._grid = {'items': []}

        def _run():
            if source['items'] is None:
                with data.Database() as db:
                    source['items'] = source['load'](db)
            if self._stale(generation):
                return
            data.apply_history(source['items'])
            start = (page - 1) * GRID_PAGE_SIZE
            end = start + GRID_PAGE_SIZE
            items = source['items'][start:end]
            if page > 1:
                items.insert(0, data.make_item(data.KIND_BACK, _string(30673)))
            if len(source['items']) > end:
                items.append(data.make_item(data.KIND_NEXT, _string(30674)))
            with self._lock:
                if self._stale(generation):
                    return
                self._grid['items'] = items
                panel.addItems([self._make_listitem(item) for item in items])
                self.setProperty('loading', '')
                if not source['items']:
                    self.setProperty('empty', 'true')
                    self.setFocusId(NAV_LIST)
                    return
                if not self._restore_focus(GRID_PANEL, panel, len(items)):
                    panel.selectItem(1 if page > 1 and len(items) > 1 else 0)
                    self.setFocusId(GRID_PANEL)

        self._start(_run)

    # Detail page

    def _open_detail(self, item):
        self._push({'type': 'detail', 'item': item})

    def _show_detail(self, view):
        with self._lock:
            self._generation += 1
            generation = self._generation
        item = view['item']
        self.setProperty('view', 'detail')
        self.setProperty('page_title', '')
        self.setProperty('empty', '')
        self.setProperty('empty_text', '')
        self._set_detail_properties(item)
        for list_id in DETAIL_LISTS:
            self.getControl(list_id).reset()
        self.setProperty('has_episodes', '')
        self.setProperty('loading', 'true')

        def _run():
            with data.Database() as db:
                # the current data, e.g. after the database was updated
                film = data.film_by_id(db, item['filmid']) or item
                episodes = []
                if film['showids']:
                    episodes = [e for e in data.show_films(db, film['showids'], [film['channel']], limit=60)
                                if e['filmid'] != film['filmid']]
            data.apply_history([film] + episodes)
            if self._stale(generation):
                return
            info = {'film': film, 'episodes': episodes[:data.ROW_MAX_ITEMS]}
            with self._lock:
                if self._stale(generation):
                    return
                self.setProperty('loading', '')
                self._detail = info
                view['item'] = film
                self._set_detail_properties(film)
                self._fill_detail(info)

        self._start(_run)

    def _set_detail_properties(self, item):
        self.setProperty('detail_title', item['label'])
        self.setProperty('detail_show', item['show'] if item['show'].lower() != item['label'].lower() else '')
        self.setProperty('detail_plot', item['plot'])
        self.setProperty('detail_meta', item['meta'])
        self.setProperty('detail_color', item['color'])
        self.setProperty('detail_logo', item['logo'])
        self.setProperty('detail_channel', data.channel_info(item['channel'])[0])
        self.setProperty('detail_episodes_title', _string(30667) % item['show'] if item['show'] else '')

    def _fill_detail(self, info):
        film = info['film']
        buttons = []
        if film['resume'] > 0:
            buttons.append(('resume', _string(30661) % _format_time(film['resume'])))
            buttons.append(('start', _string(30662)))
        else:
            buttons.append(('play', _string(30660)))
        if film['url_sub'] and not appContext.MVSETTINGS.getAutoSub():
            buttons.append(('subtitles', _string(30663)))
        if len(self._qualities(film)) > 1:
            buttons.append(('quality', _string(30664)))
        if film['show'] and info['episodes']:
            buttons.append(('show', _string(30666)))
        buttons.append(('download', _string(30665)))
        self._buttons = [b[0] for b in buttons]
        control = self.getControl(BUTTON_LIST)
        control.reset()
        for action, label in buttons:
            list_item = xbmcgui.ListItem(label=label, offscreen=True)
            list_item.setProperty('button', action)
            control.addItem(list_item)

        control = self.getControl(EPISODE_LIST)
        control.reset()
        control.addItems([self._make_listitem(e) for e in info['episodes']])
        self.setProperty('has_episodes', 'true' if info['episodes'] else '')

        restore = self._restore
        if restore and restore[0] in DETAIL_LISTS:
            self._restore = None
            target = self.getControl(restore[0])
            if target.size():
                target.selectItem(min(restore[1], target.size() - 1))
                self.setFocusId(restore[0])
                return
        self.setFocusId(BUTTON_LIST)

    def _on_button(self):
        info = self._detail
        position = self.getControl(BUTTON_LIST).getSelectedPosition()
        if not info or not 0 <= position < len(self._buttons):
            return
        action = self._buttons[position]
        film = info['film']
        if action == 'resume':
            self._play(film, resume=film['resume'])
        elif action in ('play', 'start'):
            self._play(film)
        elif action == 'subtitles':
            self._play(film, resume=film['resume'], subtitles=True)
        elif action == 'quality':
            qualities = self._qualities(film)
            choice = xbmcgui.Dialog().select(_string(30664), [label for label, _ in qualities])
            if choice >= 0:
                self._play(film, resume=film['resume'], url=qualities[choice][1])
        elif action == 'show':
            self._open_show(film)
        elif action == 'download':
            self._download(film)

    @staticmethod
    def _qualities(film):
        qualities = []
        for string_id, url in ((30349, film['url_hd']), (30350, film['url']), (30351, film['url_sd'])):
            if url and url not in [u for _, u in qualities]:
                qualities.append((_string(string_id), url))
        return qualities

    # Items

    def _make_listitem(self, item):
        list_item = xbmcgui.ListItem(label=item['label'], label2=item['subtitle'], offscreen=True)
        list_item.setArt({'thumb': item['logo'], 'icon': item['logo']})
        properties = {
            'kind': item['kind'],
            'color': item['color'] or 'FF2A3B55',
            'logo': item['logo'],
            'logo_small': data.small_logo(item['logo']),
            'tile_top': item['tile_top'],
            'meta': item['meta'],
            'channel_name': data.channel_info(item['channel'])[0] if item['channel'] else '',
            'progress': str(item['progress']) if item['progress'] else '',
            'live': 'true' if item['kind'] == data.KIND_LIVE else '',
        }
        for key, value in properties.items():
            list_item.setProperty(key, value)
        info_tag = list_item.getVideoInfoTag()
        info_tag.setTitle(item['label'])
        info_tag.setPlot(item['plot'])
        if item['duration']:
            info_tag.setDuration(int(item['duration']))
        if item['show']:
            info_tag.setTvShowTitle(item['show'])
        if item['progress']:
            # drives ListItem.PercentPlayed of the progress bar
            info_tag.setResumePoint(float(item['progress']), 100.0)
        return list_item

    def _focused_item(self, control_id):
        if control_id == GRID_PANEL:
            items = self._grid['items'] if self._grid else []
        elif control_id in ROW_LISTS:
            items = self._row_data.get(control_id) or []
        elif control_id == EPISODE_LIST:
            items = (self._detail or {}).get('episodes') or []
        else:
            return None
        position = self.getControl(control_id).getSelectedPosition()
        return items[position] if 0 <= position < len(items) else None

    def _activate(self, item, from_episodes=False):
        kind = item['kind']
        if kind == data.KIND_ACTION:
            self._run_action(item)
        elif kind == data.KIND_ALL:
            self._open_all(item)
        elif kind == data.KIND_NEXT:
            view = self._stack[-1]
            self._open_grid(view['title'], None, page=view['page'] + 1, source=view['source'])
        elif kind == data.KIND_BACK:
            self._close_view()
        elif kind == data.KIND_FILM:
            if from_episodes:
                self._play_with_resume(item)
            else:
                self._open_detail(item)
        elif kind == data.KIND_LIVE:
            self._play(item, live=True)
        elif kind == data.KIND_CHANNEL:
            self._open_channel(item)
        elif kind == data.KIND_SHOW:
            self._open_show(item)
        elif kind == data.KIND_LETTER:
            self._open_letter(item)

    def _context_menu(self, item):
        kind = item['kind']
        entries = []
        if kind == data.KIND_FILM:
            if item['resume'] > 0:
                entries.append((_string(30661) % _format_time(item['resume']), lambda: self._play(item, resume=item['resume'])))
                entries.append((_string(30662), lambda: self._play(item)))
            else:
                entries.append((_string(30660), lambda: self._play(item)))
            entries.append((_string(30670), lambda: self._open_detail(item)))
            if item['url_sub']:
                entries.append((_string(30663), lambda: self._play(item, resume=item['resume'], subtitles=True)))
            if item['show'] and item['showids']:
                entries.append((_string(30666), lambda: self._open_show(item)))
            entries.append((_string(30665), lambda: self._download(item)))
            if item['progress']:
                entries.append((_string(30669), lambda: self._remove_history(item)))
        elif kind == data.KIND_LIVE:
            entries.append((_string(30660), lambda: self._play(item, live=True)))
        elif kind == data.KIND_ACTION and item['action'] == 'search_query':
            entries.append((_string(30644), lambda: self._remove_search(item['label'])))
        if not entries:
            return
        choice = xbmcgui.Dialog().contextmenu([label for label, _ in entries])
        if choice >= 0:
            entries[choice][1]()

    def _run_action(self, item):
        action = item['action']
        addon_id = appContext.ADDONCLASS.getAddonInfo('id')
        if action == 'search':
            query = xbmcgui.Dialog().input(_string(30643)).strip()
            if len(query) >= 3:
                self._search(query)
            elif query:
                xbmcgui.Dialog().notification(_string(30614), _string(30643), time=3000, sound=False)
        elif action == 'search_query':
            self._search(item['label'])
        elif action == 'search_channel':
            channel = item['channel']
            query = xbmcgui.Dialog().input(_string(30643)).strip()
            if len(query) >= 3:
                name = data.channel_info(channel)[0]
                self._open_grid('%s  ·  %s' % (name, query), lambda db: data.search_films(db, query, channel))
            elif query:
                xbmcgui.Dialog().notification(_string(30614), _string(30643), time=3000, sound=False)
        elif action == 'saved_search':
            entry = item['entry']
            self._open_grid(item['label'], lambda db: data.saved_search_films(db, entry))
        elif action == 'settings':
            appContext.ADDONCLASS.openSettings()
        elif action == 'classic':
            xbmc.executebuiltin('ActivateWindow(Videos,plugin://%s/?classic=1,return)' % addon_id)
        elif action == 'dbinfo':
            xbmc.executebuiltin('RunPlugin(plugin://%s/?mode=action-dbinfo)' % addon_id)
        elif action == 'dbupdate':
            xbmc.executebuiltin('RunPlugin(plugin://%s/?mode=action-dbupdate)' % addon_id)
        elif action == 'refresh':
            self._cache = {}
            self._switch_tab(self._current_tab or TAB_HOME, force=True)

    def _search(self, query):
        data.save_search(query)
        self._open_grid(query, lambda db: data.search_films(db, query))

    def _remove_search(self, query):
        data.remove_search(query)
        self._remember_focus()
        self._show_rows(lambda: self._tab_rows(TAB_SEARCH))

    def _remove_history(self, item):
        data.remove_from_history(item['filmid'])
        self._refresh_view()

    def _download(self, item):
        choice = xbmcgui.Dialog().contextmenu([_string(30922), _string(30924)])
        if choice < 0:
            return
        query = urlencode({'mode': 'downloadmv' if choice == 0 else 'downloadep', 'id': item['filmid']})
        xbmc.executebuiltin('RunPlugin(plugin://%s/?%s)' % (appContext.ADDONCLASS.getAddonInfo('id'), query))

    # Playback

    def _play_with_resume(self, item):
        if item['resume'] > 0:
            choice = xbmcgui.Dialog().contextmenu([_string(30661) % _format_time(item['resume']), _string(30662)])
            if choice < 0:
                return
            self._play(item, resume=item['resume'] if choice == 0 else 0)
        else:
            self._play(item)

    def _play(self, item, resume=0, subtitles=False, url=None, live=False):
        settings = appContext.MVSETTINGS
        if url is None:
            if item['url_hd'] and settings.getPreferHd():
                url = item['url_hd']
            else:
                url = item['url'] or item['url_sd'] or item['url_hd']
        if not url:
            return
        self._remember_focus()
        path = url + settings.getUserAgentString()
        list_item = xbmcgui.ListItem(label=item['label'], path=path, offscreen=True)
        list_item.setArt({'thumb': item['logo'], 'icon': item['logo']})
        info_tag = list_item.getVideoInfoTag()
        info_tag.setTitle(item['label'])
        info_tag.setPlot(item['plot'])
        info_tag.setStudios([data.channel_info(item['channel'])[0]])
        if item['show'] and not live:
            info_tag.setTvShowTitle(item['show'])
        if item['duration']:
            info_tag.setDuration(int(item['duration']))
        if resume > 0:
            list_item.setProperty('StartOffset', str(resume))
        if not live and item['url_sub'] and (subtitles or settings.getAutoSub()):
            subtitle = self._download_subtitle(item)
            if subtitle:
                list_item.setSubtitles([subtitle])
        _log('Playing {} ({}, resume {})', item['filmid'], url, resume)
        self._player.track(None if live else item)
        self._player.play(path, list_item)

    @staticmethod
    def _download_subtitle(item):
        from resources.lib.downloader import Downloader
        from resources.lib.model.film import Film

        class _Plugin(object):
            database = None
            language = staticmethod(_string)

        film = Film()
        film.filmid, film.url_sub = item['filmid'], item['url_sub']
        datapath = appContext.MVSETTINGS.getDatapath()
        ttml = os.path.join(datapath, 'subtitle.ttml')
        srt = os.path.join(datapath, 'subtitle.srt')
        if Downloader(_Plugin()).download_subtitle(film, ttml, srt, 'subtitle'):
            return srt
        return None

    def _on_playback_end(self):
        if self._closing:
            return
        if xbmc.Monitor().waitForAbort(PLAYBACK_END_DELAY):
            return
        if self._closing or xbmc.getCondVisibility('Player.HasMedia') or xbmcgui.getCurrentWindowId() != self._window_id:
            # refreshed when the window is shown again
            self._refresh_pending = True
            return
        self._refresh_pending = False
        self._refresh_view()

    def _refresh_view(self):
        """ Reload the current view, e.g. the progress of "continue watching" after playback """
        if not self._restore:
            self._remember_focus()
        if self._stack:
            self._render(self._stack[-1])
        elif self._current_tab:
            self._show_rows(lambda: self._tab_rows(self._current_tab))

    # Helpers

    def _remember_focus(self):
        focus_id = self.getFocusId()
        if focus_id in ROW_LISTS + (GRID_PANEL,) + DETAIL_LISTS:
            self._restore = (focus_id, self.getControl(focus_id).getSelectedPosition(), self._row_offset)
        else:
            self._restore = None

    def _restore_focus(self, control_id, control, num_items):
        restore = self._restore
        if not restore or restore[0] != control_id:
            return False
        self._restore = None
        position = min(restore[1], num_items - 1)
        if position >= 0:
            control.selectItem(position)
        if control_id in ROW_LISTS:
            self._set_row_offset(ROW_LISTS.index(control_id))
        self.setFocusId(control_id)
        return True

    def _stale(self, generation):
        return self._closing or generation != self._generation

    def _start(self, target, *args):
        def _run():
            try:
                target(*args)
            # pylint: disable=broad-except
            except Exception as err:
                import traceback
                appContext.MVLOGGER.get_new_logger('Home').error('Task failed: {}\n{}', err, traceback.format_exc())
                self.setProperty('loading', '')

        thread = threading.Thread(target=_run)
        thread.daemon = True
        thread.start()
        return thread


class _Player(xbmc.Player):
    """ Starts playback and remembers the position of the film for "Continue watching" """

    def __init__(self, on_playback_end):
        super(_Player, self).__init__()
        self._on_playback_end = on_playback_end
        self._item = None
        self._tracking = 0

    def track(self, item):
        self._item = item
        self._tracking += 1

    def play(self, path, list_item):
        super(_Player, self).play(path, list_item)
        if self._item:
            thread = threading.Thread(target=self._track, args=(self._item, self._tracking))
            thread.daemon = True
            thread.start()

    def _track(self, item, tracking):
        """ The position can only be read while the film plays """
        monitor = xbmc.Monitor()
        position = total = 0
        started = False
        for _ in range(300):
            # wait for the start, the stream can take a while
            if self.isPlayingVideo() or monitor.waitForAbort(0.1):
                started = self.isPlayingVideo()
                break
        while started and tracking == self._tracking and not monitor.abortRequested():
            try:
                if not self.isPlayingVideo():
                    break
                position = self.getTime()
                total = self.getTotalTime() or item['duration']
            except RuntimeError:
                break
            if monitor.waitForAbort(2):
                break
        if started and position > 0:
            data.save_position(item, position, total)

    def _notify(self):
        thread = threading.Thread(target=self._on_playback_end)
        thread.daemon = True
        thread.start()

    def onPlayBackEnded(self):
        self._notify()

    def onPlayBackStopped(self):
        self._notify()

    def onPlayBackError(self):
        self._notify()


def _activate_running_window(value, timeout=3):
    """ Bring a running home window to the front, False if it is not running """
    if value.startswith('starting:'):
        try:
            return time.time() - float(value.partition(':')[2]) < 30
        except ValueError:
            return False
    if not value.isdigit():
        return False
    xbmc.executebuiltin('ActivateWindow(%s)' % value)
    monitor = xbmc.Monitor()
    for _ in range(timeout * 10):
        if xbmc.getCondVisibility('Window.IsActive(%s)' % value):
            return True
        if monitor.waitForAbort(0.1):
            break
    return False


def open_home_window():
    home = xbmcgui.Window(10000)
    running = home.getProperty(WINDOW_PROPERTY)
    if running and _activate_running_window(running):
        return
    home.setProperty(WINDOW_PROPERTY, 'starting:%f' % time.time())
    try:
        path = appContext.ADDONCLASS.getAddonInfo('path')
        window = HomeWindow('script-mediathekview-home.xml', path, 'default', '1080i')
        window.doModal()
        del window
    finally:
        home.clearProperty(WINDOW_PROPERTY)
