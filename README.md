# Workspace Icon Daemon

[TODO: make this a gif]
![Example bar with workspace icons](images/bar_example_image.png)
*Example: i3bar showing workspaces with icons for currently running programs*

[TODO: example swaybar image]
*Example: swaybar showing workspaces with icons for currently running programs*

Workspace Icon Daemon shows the applications on each workspace as color icons. It supports both i3 and Sway through their shared IPC protocol and generates a small icon font from the desktop icons already installed on the system.

This is a new project that replaces `i3-workspace-icons` and `sway-workspace-icons`. It intentionally uses new command names, package names, font names, and XDG directories; data from either earlier project is not imported.

## Features

- Automatically detects i3 or Sway, with an explicit override when needed
- Reads `WM_CLASS` on i3 and both Wayland `app_id` and XWayland `WM_CLASS` on Sway
- Supports i3bar, Waybar, or no automatic bar restart
- Resolves application icons through desktop files and common icon themes
- Generates a color bitmap font from SVG and PNG icons
- Shows duplicate applications individually, once, or with subscript/superscript counts

## Installation

The system needs Fontconfig and Cairo. On Debian or Ubuntu:

```sh
sudo apt install fontconfig libcairo2
```

Install the Python package:

```sh
git clone https://github.com/David0tt/workspace-icon-daemon
pip install ./workspace-icon-daemon
```

## Configuration

Use `WorkspaceIconDaemon` as the bar font. The daemon keeps the numeric prefix in workspace names, so workspace keybindings should select workspaces by number.

For i3:

```i3config
bar {
    font WorkspaceIconDaemon 20
    height 30
}

exec_always --no-startup-id workspace-icon-daemon
bindsym $mod+1 workspace number 1
bindsym $mod+2 workspace number 2
```

For Sway with Waybar, add the font to `~/.config/waybar/style.css`:

```css
* {
    font-family: WorkspaceIconDaemon, sans-serif;
}
```

Then add this to the Sway config:

```swayconfig
exec_always workspace-icon-daemon
bindsym $mod+1 workspace number 1
bindsym $mod+2 workspace number 2
```

Automatic detection chooses i3bar for i3 and Waybar for Sway. Override either decision when necessary:

```sh
workspace-icon-daemon --compositor sway --bar none
workspace-icon-daemon --compositor sway --bar i3bar
```

`--bar none` installs and refreshes the font cache without managing a bar process.

## Options

```text
--compositor {auto,i3,sway}
--bar {auto,i3bar,waybar,none}
--unique-icons {nonunique,numbers_superscript,numbers_subscript,unique}
--no-placeholder-icon
--rebuild
--full-rebuild
--program-icon-map PATH
--base-font PATH
--font-output PATH
--font-family-name NAME
--verbose
```

The new persistent paths are:

- `$XDG_CONFIG_HOME/workspace-icon-daemon/program_icon_map.yaml`
- `$XDG_CACHE_HOME/workspace-icon-daemon/WorkspaceIconDaemon.ttf`
- `$XDG_DATA_HOME/fonts/WorkspaceIconDaemon.ttf`

The usual XDG defaults apply when those environment variables are unset.

## How it works

The daemon listens for window and workspace events over i3 IPC. For each application it finds a desktop entry, resolves the associated SVG or PNG icon, assigns a Private Use Area codepoint, and rebuilds a color bitmap font when a previously unseen application appears. It then renames each workspace to a value such as `2: <icons>`.

On SIGINT or SIGTERM it restores each workspace to its numeric name before exiting.

## Development

```sh
python -m pip install -e .
python -m unittest discover
```
