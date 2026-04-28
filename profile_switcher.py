# Profile Switcher - Terminator plugin
# Switches Terminator profile based on the foreground command (and optionally
# its arguments) running in the terminal. Detection is local: it reads
# /proc/<pgid>/comm and /proc/<pgid>/cmdline of the foreground process group
# of the terminal's pty -- no shell setup or remote configuration required.
#
# Rules are configured via the right-click menu:
#   Profile Switcher -> Preferences
#
# Each rule has:
#   - Command:  exact command name (matched against /proc/<pid>/comm,
#               which is truncated at 15 characters)
#   - Argument: fnmatch glob matched against the joined argv (excluding the
#               command itself). Leave empty to match any invocation.
#   - Profile:  Terminator profile to apply.
#
# First matching rule wins. When no rule matches (or the shell is foreground
# again), the profile reverts to "default" -- but only if a rule had
# previously been applied to that terminal, so manually-chosen profiles
# aren't clobbered when nothing relevant is happening.
#
# Examples:
#   Command=ssh     Argument=*staging* Profile=yellow
#   Command=ssh     Argument=*prod*    Profile=red
#   Command=top     Argument=(empty)   Profile=dark
#   Command=python3 Argument=(empty)   Profile=solarized

import os
import fnmatch

from gi.repository import Gtk, GObject, GLib

import terminatorlib.plugin as plugin
from terminatorlib.config import Config
from terminatorlib.terminator import Terminator
from terminatorlib.translation import _
from terminatorlib.util import dbg, err

AVAILABLE = ['ProfileSwitcher']

DEFAULT_PROFILE = 'default'
COMMAND_POLL_MS = 1000

(COL_COMMAND, COL_ARGUMENT, COL_PROFILE) = (0, 1, 2)


class ProfileSwitcher(plugin.MenuItem):
    """Switch Terminator profile based on the foreground command + argv."""
    capabilities = ['terminal_menu']

    rules = None              # ordered list of dicts: {command, argument, profile}
    watched = None            # set of terminals already wired up
    state = None              # terminal -> {'last_applied': prof|None,
                              #              'last_signature': (cmd, args)|None}
    handler_ids = None        # terminal -> [(obj, hid), ...] for cleanup
    timer_ids = None          # terminal -> GLib source id for command polling

    def __init__(self):
        plugin.MenuItem.__init__(self)
        self.rules = []
        self.watched = set()
        self.state = {}
        self.handler_ids = {}
        self.timer_ids = {}
        self._load_config()
        self._update_watched()

    def unload(self):
        for _terminal, entries in list(self.handler_ids.items()):
            for (obj, hid) in entries:
                try:
                    obj.disconnect(hid)
                except Exception:
                    pass
        for tid in self.timer_ids.values():
            try:
                GLib.source_remove(tid)
            except Exception:
                pass
        self.handler_ids.clear()
        self.timer_ids.clear()
        self.watched.clear()
        self.state.clear()

    # ------------------------------------------------------------------ config

    def _load_config(self):
        cfg = Config()
        sections = cfg.plugin_get_config(self.__class__.__name__)
        self.rules = []
        if not isinstance(sections, dict):
            return
        ordered = []
        for name, item in sections.items():
            if not isinstance(item, dict):
                continue
            if 'profile' not in item:
                continue
            command = item.get('command')
            argument = item.get('argument', '')
            # Backward-compat: old schema had {pattern, type, profile}
            if command is None and 'pattern' in item:
                old_type = item.get('type', 'host')
                if old_type == 'command':
                    command = item['pattern']
                else:
                    err('ProfileSwitcher: skipping legacy host rule %r '
                        '(re-add as ssh + glob argument)' % name)
                    continue
            if not command:
                continue
            try:
                pos = int(item.get('position', len(ordered)))
            except (TypeError, ValueError):
                pos = len(ordered)
            ordered.append((pos, command, argument, item['profile']))
        ordered.sort(key=lambda x: x[0])
        self.rules = [{'command': c, 'argument': a, 'profile': p}
                      for (_pos, c, a, p) in ordered]
        dbg('ProfileSwitcher: loaded %d rules' % len(self.rules))

    def _save_config(self):
        cfg = Config()
        cfg.plugin_del_config(self.__class__.__name__)
        for i, rule in enumerate(self.rules):
            cfg.plugin_set(self.__class__.__name__,
                           'rule_%d' % i,
                           {'command': rule['command'],
                            'argument': rule['argument'],
                            'profile': rule['profile'],
                            'position': i})
        cfg.save()

    # ---------------------------------------------------------------- watching

    def _ensure_state(self, terminal):
        if terminal not in self.state:
            self.state[terminal] = {'last_applied': None,
                                    'last_signature': None}
        return self.state[terminal]

    def _update_watched(self):
        """Start polling on any terminals we haven't seen yet."""
        for terminal in Terminator().terminals:
            if terminal in self.watched:
                continue
            try:
                hid_focus = terminal.connect('focus-out',
                                             self._on_focus_out_delayed, None)
                self.handler_ids.setdefault(terminal, []).append(
                    (terminal, hid_focus))
                tid = GLib.timeout_add(COMMAND_POLL_MS,
                                       self._poll_command, terminal)
                self.timer_ids[terminal] = tid
                self._ensure_state(terminal)
                self.watched.add(terminal)
                dbg('ProfileSwitcher: watching %s' % terminal)
            except Exception as ex:
                err('ProfileSwitcher: failed to wire terminal: %s' % ex)

    def _on_focus_out_delayed(self, _terminal, _event, _arg=None):
        GObject.idle_add(self._update_watched_idle)
        return False

    def _update_watched_idle(self):
        self._update_watched()
        return False

    # ----------------------------------------------------- foreground command

    def _foreground(self, terminal):
        """Return (cmd_name, args_joined) for the foreground process group,
        or (None, None) if the shell itself is foreground or detection fails.
        args_joined is argv[1:] joined with spaces.
        """
        try:
            pty = terminal.get_vte().get_pty()
            if pty is None:
                return (None, None)
            fd = pty.get_fd()
            pgrp = os.tcgetpgrp(fd)
        except Exception:
            return (None, None)
        if pgrp <= 0:
            return (None, None)
        if terminal.pid is not None and pgrp == terminal.pid:
            return (None, None)  # shell itself is foreground
        try:
            with open('/proc/%d/comm' % pgrp, 'r') as f:
                cmd = f.read().strip() or None
        except Exception:
            return (None, None)
        args = ''
        try:
            with open('/proc/%d/cmdline' % pgrp, 'rb') as f:
                raw = f.read()
            argv = [p.decode('utf-8', 'replace')
                    for p in raw.split(b'\0') if p]
            if len(argv) > 1:
                args = ' '.join(argv[1:])
        except Exception:
            pass
        return (cmd, args)

    def _poll_command(self, terminal):
        if terminal not in self.watched:
            return False  # stop the timer
        state = self._ensure_state(terminal)
        cmd, args = self._foreground(terminal)
        signature = (cmd, args)
        if signature == state['last_signature']:
            return True
        state['last_signature'] = signature
        target = self._match(cmd, args) if cmd else None
        self._apply(terminal, target)
        return True

    # ---------------------------------------------------------- profile switch

    def _match(self, cmd, args):
        """Return the profile name for the first matching rule, or None."""
        cmd_lc = (cmd or '').lower()
        args_lc = (args or '').lower()
        for rule in self.rules:
            if rule['command'].lower() != cmd_lc:
                continue
            arg_pat = (rule['argument'] or '').strip()
            if not arg_pat:
                return rule['profile']
            if fnmatch.fnmatchcase(args_lc, arg_pat.lower()):
                return rule['profile']
        return None

    def _apply(self, terminal, target):
        """Apply target profile, or revert to default if a rule was active."""
        state = self._ensure_state(terminal)
        if target is None:
            if state['last_applied'] is None:
                return  # never touched this terminal
            target = DEFAULT_PROFILE
        if target == state['last_applied']:
            return
        if self._set_profile(terminal, target):
            state['last_applied'] = (target if target != DEFAULT_PROFILE
                                     else None)

    def _set_profile(self, terminal, profile):
        try:
            available = Config().list_profiles()
        except Exception:
            available = [DEFAULT_PROFILE]
        if profile not in available:
            err('ProfileSwitcher: profile %r missing, falling back to %r'
                % (profile, DEFAULT_PROFILE))
            profile = DEFAULT_PROFILE
        if terminal.get_profile() == profile:
            return True
        dbg('ProfileSwitcher: switching terminal to %s' % profile)
        try:
            terminal.force_set_profile(None, profile)
            return True
        except Exception as ex:
            err('ProfileSwitcher: force_set_profile failed: %s' % ex)
            return False

    # ----------------------------------------------------------- context menu

    def callback(self, menuitems, _menu, _terminal):
        self._update_watched()
        item = Gtk.MenuItem.new_with_mnemonic(_('_Profile Switcher'))
        submenu = Gtk.Menu()
        item.set_submenu(submenu)
        prefs = Gtk.MenuItem.new_with_mnemonic(_('_Preferences...'))
        prefs.connect('activate', self.configure)
        submenu.append(prefs)
        menuitems.append(item)

    # ----------------------------------------------------------------- dialog

    def configure(self, widget, _data=None):
        dialog = Gtk.Dialog(
            _('Profile Switcher - Rules'),
            None,
            Gtk.DialogFlags.MODAL,
            (_('_Cancel'), Gtk.ResponseType.REJECT,
             _('_OK'), Gtk.ResponseType.ACCEPT))
        if widget:
            try:
                dialog.set_transient_for(widget.get_toplevel())
            except Exception:
                pass
        dialog.set_default_size(640, 360)

        store = Gtk.ListStore(str, str, str)
        for rule in self.rules:
            store.append([rule['command'], rule['argument'], rule['profile']])

        treeview = Gtk.TreeView(model=store)
        treeview.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        # Command (editable text)
        renderer_cmd = Gtk.CellRendererText()
        renderer_cmd.set_property('editable', True)
        renderer_cmd.connect('edited', self._on_text_edited, store, COL_COMMAND)
        col_cmd = Gtk.TreeViewColumn(_('Command'),
                                     renderer_cmd, text=COL_COMMAND)
        treeview.append_column(col_cmd)

        # Argument (editable text, glob pattern)
        renderer_arg = Gtk.CellRendererText()
        renderer_arg.set_property('editable', True)
        renderer_arg.connect('edited', self._on_text_edited, store, COL_ARGUMENT)
        col_arg = Gtk.TreeViewColumn(_('Argument (glob, empty = any)'),
                                     renderer_arg, text=COL_ARGUMENT)
        col_arg.set_expand(True)
        treeview.append_column(col_arg)

        # Profile (combo populated from existing profiles)
        profile_store = Gtk.ListStore(str)
        for p in Config().list_profiles():
            profile_store.append([p])
        renderer_prof = Gtk.CellRendererCombo()
        renderer_prof.set_property('editable', True)
        renderer_prof.set_property('model', profile_store)
        renderer_prof.set_property('text-column', 0)
        renderer_prof.set_property('has-entry', False)
        renderer_prof.connect('edited', self._on_text_edited, store, COL_PROFILE)
        col_prof = Gtk.TreeViewColumn(_('Profile'), renderer_prof,
                                      text=COL_PROFILE)
        treeview.append_column(col_prof)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(treeview)

        hbox = Gtk.HBox(spacing=6)
        hbox.pack_start(scroll, True, True, 0)

        button_box = Gtk.VBox(spacing=4)
        for label, handler in (
                (_('Add'),    self._on_add),
                (_('Delete'), self._on_delete),
                (_('Up'),     self._on_up),
                (_('Down'),   self._on_down)):
            btn = Gtk.Button(label=label)
            btn.connect('clicked', handler, treeview)
            button_box.pack_start(btn, False, False, 0)
        hbox.pack_start(button_box, False, False, 0)

        dialog.vbox.pack_start(hbox, True, True, 6)

        hint = Gtk.Label()
        hint.set_markup(_(
            '<small>'
            'First matching rule wins. No match reverts to <b>default</b>.\n'
            '<b>Command</b> is matched exactly against the foreground process '
            'name (<tt>/proc/&lt;pid&gt;/comm</tt>, truncated at 15 chars).\n'
            '<b>Argument</b> is a case-insensitive glob matched against the '
            'joined argv (excluding the command). Empty matches any '
            'invocation.\n'
            'Examples: <tt>ssh</tt> + <tt>*staging*</tt>, '
            '<tt>ssh</tt> + <tt>*prod*</tt>, '
            '<tt>python3</tt> + <i>(empty)</i>.'
            '</small>'))
        hint.set_line_wrap(True)
        hint.set_xalign(0)
        dialog.vbox.pack_start(hint, False, False, 6)

        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            self.rules = []
            it = store.get_iter_first()
            while it is not None:
                cmd = (store.get_value(it, COL_COMMAND) or '').strip()
                arg = (store.get_value(it, COL_ARGUMENT) or '').strip()
                prof = store.get_value(it, COL_PROFILE) or DEFAULT_PROFILE
                if cmd:
                    self.rules.append({'command': cmd,
                                       'argument': arg,
                                       'profile': prof})
                it = store.iter_next(it)
            self._save_config()
            # Force re-evaluation on next poll for all watched terminals
            for s in self.state.values():
                s['last_signature'] = None
        dialog.destroy()

    def _on_text_edited(self, _renderer, path, new_text, store, column):
        store[path][column] = new_text

    def _on_add(self, _button, treeview):
        store = treeview.get_model()
        store.append(['command', '', DEFAULT_PROFILE])

    def _on_delete(self, _button, treeview):
        sel = treeview.get_selection()
        (store, it) = sel.get_selected()
        if it is not None:
            store.remove(it)

    def _on_up(self, _button, treeview):
        sel = treeview.get_selection()
        (store, it) = sel.get_selected()
        if it is None:
            return
        idx = store.get_path(it).get_indices()[0]
        if idx == 0:
            return
        store.swap(it, store.get_iter(idx - 1))

    def _on_down(self, _button, treeview):
        sel = treeview.get_selection()
        (store, it) = sel.get_selected()
        if it is None:
            return
        nxt = store.iter_next(it)
        if nxt is not None:
            store.swap(it, nxt)
