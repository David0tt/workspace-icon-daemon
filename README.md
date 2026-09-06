# Workspace Icon Daemon

[TODO: make this a gif]
![Example bar with workspace icons](images/bar_example_image.png)
*Example: i3bar showing workspaces with icons for currently running programs*

[TODO: example swaybar image]
*Example: swaybar showing workspaces with icons for currently running programs*

Dynamically show running application icons in workspace names and window
titlebars on **Sway and i3**.

The daemon finds system application icons, builds a color icon font, and updates
workspace names when windows open, close or move to different workspaces.

This approach should work on almost any bar that has font rendering capabilities. i3bar and waybar are explicitly tested. Crucially, this does not require image rendering capabilities by the bar, since a font with the appropriate program icons is created on the fly. 

Minor modifications may be needed when using a bar different from i3bar or swaybar, to correctly restart the bar. This is required, since whenever a compeletely new program is opened, the icon font is recreated with this additional icon and a reload of the bar is required afterwards. 


## Features
- Put program icons into the workspaces on the bar
- Put program icons into the window titlebars
- Automatically detects and displays the correct icon for any program.
- Automatically detects i3 or Sway, with an explicit override when needed.
- Supports i3bar, Waybar, or no automatic bar restart. 
- Supports program count indicators as sub-/superscripts when multiple instances of the same program are running.

## Installation

Workspace Icon Daemon requires Python 3.10 or newer, pip, Python's `venv`
module, Fontconfig, Cairo, and procps (`pgrep`/`pkill`).

On Debian or Ubuntu, install them with:

```sh
sudo apt install python3 python3-pip python3-venv fontconfig libcairo2 procps
```

On Arch Linux, install them with:

```sh
sudo pacman -S python python-pip fontconfig cairo procps-ng
```

Clone the repository, create a dedicated virtual environment, and install the
package into it:

```sh
git clone https://github.com/David0tt/workspace-icon-daemon
python3 -m venv ~/.local/share/workspace-icon-daemon/venv
~/.local/share/workspace-icon-daemon/venv/bin/python -m pip install ./workspace-icon-daemon
```

The daemon executable is then available at
`~/.local/share/workspace-icon-daemon/venv/bin/workspace-icon-daemon`. The
configuration examples below use this path, so activating the virtual
environment is not required when the window manager starts the daemon.

## Configuration

Use `WorkspaceIconDaemon` as the bar font. 

For i3, edit the i3 config:

```i3config
# Pango markup is required for the default titlebar icons.
font pango:monospace 10

bar {
    font WorkspaceIconDaemon 20
    height 30
}

exec_always --no-startup-id ~/.local/share/workspace-icon-daemon/venv/bin/workspace-icon-daemon
```

For Sway with Waybar, add the font to `~/.config/waybar/style.css`:

```css
* {
    font-family: WorkspaceIconDaemon, sans-serif;
}
```

Then add this to the Sway config:

```swayconfig
font pango:monospace 18

exec_always ~/.local/share/workspace-icon-daemon/venv/bin/workspace-icon-daemon
```

Both workspace and titlebar icons are enabled by default. You can deactivate either with the `--no-workspace-icons` and `--no-titlebar-icons` flags.

The daemon applies a per-window `title_format` such as
`<span font_family='WorkspaceIconDaemon'>ICON</span> %title`. The generated
font is used only for the icon; normal title text continues to use the font
configured in Sway. A normal titlebar must be enabled for the icon to be
visible. 

Automatic detection chooses i3bar for i3 and Waybar for Sway. Override either decision when necessary:

```sh
~/.local/share/workspace-icon-daemon/venv/bin/workspace-icon-daemon --compositor sway --bar none
~/.local/share/workspace-icon-daemon/venv/bin/workspace-icon-daemon --compositor sway --bar i3bar
```

`--bar none` installs and refreshes the font cache without managing a bar process.


On either window manager, use workspace **numbers** for switching and moving
windows, because names change dynamically:

```text
bindsym $mod+1 workspace number 1
bindsym $mod+2 workspace number 2
bindsym $mod+Shift+1 move container to workspace number 1
bindsym $mod+Shift+2 move container to workspace number 2
# Repeat for your remaining numbered workspaces.
```

## Options
The most important options you might want to use. Use `--help` for a full list.
```bash
workspace-icon-daemon --help                               # Show all options
workspace-icon-daemon --no-titlebar-icons                  # Don't put icons into the titlebars
workspace-icon-daemon --no-workspace-icons                 # Don't put icons into the workspaces
workspace-icon-daemon --unique-icons                       # # Display mode: nonunique | unique | numbers_subscript | numbers_superscript
workspace-icon-daemon --no-placeholder-icon                # Don't use placeholder icons when program icons are not found
workspace-icon-daemon --compositor {auto,i3,sway}          # Explicitly specify the compositor
workspace-icon-daemon --bar {auto,i3bar,waybar,none}       # Explicitly specify the bar
workspace-icon-daemon --rebuild                            # rediscover icons and rebuild the font from the saved icon map
workspace-icon-daemon --full-rebuild                       # delete the icon map and cached font, then rediscover and rebuild
workspace-icon-daemon --verbose                            # Enable debug output
``` 

The persistent paths are:

- `$XDG_CONFIG_HOME/workspace-icon-daemon/program_icon_map.yaml`
- `$XDG_CACHE_HOME/workspace-icon-daemon/WorkspaceIconDaemon.ttf`
- `$XDG_DATA_HOME/fonts/WorkspaceIconDaemon.ttf`

The usual XDG defaults apply when those environment variables are unset.


## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover
```

## Limitations

- When opening new programs, bar restarts can cause brief flicker. This is required, since the font may not be changed while the bar is using it. For simultaneous sessions, custom bar launch arguments, or bars supervised by systemd, use `--bar none` and arrange font reloads through your bar manager.
- Numbers indicating program counts use subscripts/superscripts, which disrupt equal spacing between icons. A better solution might use Unicode diacritics or embed numbers directly in icons, but this has not been implemented yet. 
- This system is relatively hacky. If you want something simpler, you can use the default approach of mapping programs to nerdfont symbols used by many other setups. 

## How It Works

Since most bars cannot display images directly, this daemon creates a custom font from program icons on-the-fly:

1. The daemon monitors window events (new, close, move) over i3 IPC
2. When a new program is detected:
   - Finds the program's `.desktop` file
   - Extracts the `Icon=` entry to locate the icon file in standard system directories
   - Assigns the program a Unicode codepoint in the Private Use Area (PUA)
   - Rebuilds the custom color bitmap font with all known icons when a previously unseen application appears
   - Stops bar (to prevent crashes when modifying the active font)
   - Installs the font
   - Restarts bar
   - Reloads the font cache with `fc-cache`
   - Restarts bar again to load the updated cache
3. Workspace names are updated with icon characters from the custom font whenever window events occur to values like `2: <icons>`
4. A program-to-icon map is persisted for consistency and fast restarts. The font is only rebuilt when a completely new program is encountered


### Possible Future Features

- [ ] Limit maximum number of icons shown per workspace
- [ ] Better icon spacing when using count indicators
- [ ] support for different bars. In theory, this works, with any bar that shows workspaces by their title, and where you can set the font (and that has a reasonable font rendering support, e.g. for emojis). However, the updating sequence needs to be modified, depending on the bar. 
- [ ] graceful restart currently does not work: If you add the workspace-icon-daemon to your config with exec_always, this could lead to multiple daemon processes being created, whenever you reload the window manager. Further, the daemon closes and starts the bar, thereby taking ownership of this child process, so on window manager reload a second bar is started. To the best of my knowledge everything works when the whole system is restarted, but of course this could be improved. 
- [ ] In general, sway is tested much better than i3, since it is my daily driver. If you come across any bugs on i3, please open an issue.


## Inspiration

This project is inspired by:
- [i3-workspace-names-daemon](https://github.com/cboddy/i3-workspace-names-daemon)
- [i3scripts/autoname_workspaces.py](https://github.com/justbuchanan/i3scripts)
- [sway-dynamic-names](https://github.com/j-waters/sway-dynamic-names)

Unlike these projects, which rely on pre-existing icon fonts (like FontAwesome or Nerd Fonts) with predefined program-to-icon mappings, my daemon uses actual program icons from your system. Therefore in contrast to these other tools it can 1. show color icons 2.show icons for almost any program automatically. If you encounter a program, where no icon or the wrong icon is shown, please let me know in an issue. 
