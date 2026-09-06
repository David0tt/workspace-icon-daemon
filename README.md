# Workspace Icon Daemon

[TODO: make this a gif]
![Example bar with workspace icons](images/bar_example_image.png)
*Example: i3bar showing workspaces with icons for currently running programs*

[TODO: example swaybar image]
*Example: swaybar showing workspaces with icons for currently running programs*

Dynamically show running application icons in workspace names and window
titlebars on **Sway and i3**.

The daemon discovers all installed desktop applications, builds a color icon font,
and dynamically updates workspace names and application titles when windows open, close or move.

This approach should work on almost any bar that has font rendering capabilities. i3bar and waybar are explicitly tested. Crucially, this does not require image rendering capabilities by the bar, since a font with the appropriate program icons is created on the fly. 

To use newly generated icon fonts, the bar or compositor needs to be restarted once by logging out and back in again.


## Features
- Put program icons into the workspaces on the bar
- Put program icons into the window titlebars
- Automatically detects and displays the correct icon for any program.
- Automatically detects i3 or Sway, with an explicit override when needed.
- Explicitly supports i3bar and Waybar, however other bars with (pango) font rendering support should also work
- Supports program count indicators as sub-/superscripts when multiple instances of the same program are running.

## Installation

Workspace Icon Daemon requires Python 3.10 or newer, pip, Python's `venv`
module, Fontconfig, Cairo, and `notify-send` for desktop notifications.

On Debian or Ubuntu, install them with:

```sh
sudo apt install python3 python3-pip python3-venv fontconfig libcairo2 libnotify-bin
```

On Arch Linux, install them with:

```sh
sudo pacman -S python python-pip fontconfig cairo libnotify
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

Override compositor detection when necessary:

```sh
~/.local/share/workspace-icon-daemon/venv/bin/workspace-icon-daemon --compositor sway
~/.local/share/workspace-icon-daemon/venv/bin/workspace-icon-daemon --compositor i3
```

### First start and newly installed applications

On the first start, the daemon scans all XDG desktop entries, builds and installs
the font, sends a desktop notification, and exits without renaming anything.
Log out and back in once; the second start enters the normal monitoring loop.

When an application not present in the session's loaded font is discovered (e.g.
after installing a new program), the daemon installs an updated font for the next
login and sends a notification. It continues running and displays the reserved
placeholder glyph for that application in the current session.


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
workspace-icon-daemon --reset                              # stop daemon, restore workspace and title names, remove state, and exit
workspace-icon-daemon --reset-and-rebuild                  # reset, prebuild/install the font, and exit
workspace-icon-daemon --verbose                            # Enable debug output
``` 

The persistent paths are:

- `$XDG_CONFIG_HOME/workspace-icon-daemon/program_icon_map.yaml`
- `$XDG_CACHE_HOME/workspace-icon-daemon/WorkspaceIconDaemon.ttf`
- `$XDG_DATA_HOME/fonts/WorkspaceIconDaemon.ttf`
- `$XDG_CACHE_HOME/workspace-icon-daemon/daemon.pid`

The usual XDG defaults apply when those environment variables are unset.


## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover
```

## Limitations

- Newly installed application icons require a logout/login before they replace
  the placeholder glyph. 
- Numbers indicating program counts use subscripts/superscripts, which disrupt equal spacing between icons. A better solution might use Unicode diacritics or embed numbers directly in icons, but this has not been implemented yet. 
- This system is relatively hacky. If you want something simpler, you can use the default approach of mapping programs to nerdfont symbols used by many other setups. 

## How It Works

Since most bars cannot display images directly, this daemon creates a custom font from program icons on-the-fly:

1. At startup, the daemon discovers all installed programs and their icons.
   1. It first scans desktop entries in XDG precedence order:
      1. $XDG_DATA_HOME/applications
      2. Each directory in $XDG_DATA_DIRS, normally:
         - /usr/local/share/applications
         - /usr/share/applications
      3. /var/lib/snapd/desktop/applications
   2. For each `.desktop` file, the daemon extracts the `Icon=`
   and `StartupWMClass=` values.
   3. Icon discsovery. Icons are found following the following precedence:
      1. An absolute SVG or PNG path specified directly by `Icon=`.
      2. SVG application icons, in order:
         1. `hicolor/scalable/apps`
         2. Humanity application-icon directories
         3. `HighContrast/scalable/apps`
         4. `/usr/share/pixmaps`
      3. PNG application icons, in order:
         1. `hicolor` application-icon directories going through the resolutions beginning at 124x124 (Note that the target font uses a strike of 109×109 pixels, so input resolutions closest to this are preferred)
         2. `/usr/share/pixmaps`
      4. Recursive SVG fallback search through:
         1. `$XDG_DATA_HOME/icons`
         2. `$XDG_DATA_HOME/pixmaps`
         3. The corresponding `icons` and `pixmaps` directories under `$XDG_DATA_DIRS`
      5. Recursive PNG fallback search through the same directories.
      6. Note: The explicit application-ion directories are searched before recursive theme fallbacks. This prevents symbolic or monochrome theme icons from overriding full-color application icons in hicolor.
2. It reserves U+E000 for the placeholder icon and assigns stable PUA (Private Use Area) 
   Unicode  codepoints to discovered applications.
3. The first run builds the custom icon font, atomically installs it, refreshes fontconfig,
   notifies, and exits. No workspace or titlebar names are changed, since hot-swapping the
   loaded fonts for running applications (compositor, bar) is not possible and a restart 
   (logout/login) is required
4. Later runs use the installed and loaded icon font and dynamically modify workspace 
   names and titlebar names to show these icons, based on window events. 
5. A newly discovered program (e.g. after a new installation) triggers a next-session 
   font build and notification; in the current session the placeholder icon is used.

## Using the font builder standalone

The bundled font builder can also be used stand alone to create an icon font from PNG and SVG files

```sh
python -m workspace_icon_daemon.font_builder --help
```

The base font must be a bitmap color font containing CBDT and CBLC tables. The
repository includes a suitable `NotoColorEmoji.ttf` base font. Font generation is generally very tricky, and I can not guarantee it working with other fonts. 

### Building a font

You can either put the desired PNG and SVG files into a directory, or build from an explicit list, using either the `--input-folder` or `--icon-paths` flag. Example

```sh
python -m workspace_icon_daemon.font_builder \
    --icon-paths /usr/share/icons/hicolor/scalable/apps/firefox.svg /usr/share/icons/breeze/apps/16/utilities-terminal.svg \
    --base-font ./workspace_icon_daemon/NotoColorEmoji.ttf \
    --output ./MyIconFont.ttf \
    --family-name MyIconFont \
    --pua-start 0xE100 \
    --remove-original-symbols
```

PNG images are resized to the base font's bitmap strike size when necessary (for NotoColorEmoji this is 109x109); SVG images are rasterized at that size. `--remove-original-symbols` removes the base font's existing emoji glyphs and produces an icon-only font. Omit it if you want to retain the original glyphs as well.

You can use `font-manager MyIconFont.ttf` to inspect the generated font and you can use `fc-cache` to install it. If this does not work, you might want to use the font-viewer directly: `/usr/lib/font-manager/font-viewer MyIconFont.ttf`.



### Possible Future Features

- [ ] Limit maximum number of icons shown per workspace
- [ ] Better icon spacing when using count indicators
- [ ] support for different bars. In theory, this works, with any bar that shows workspaces by their title, and where you can set the font (and that has a reasonable font rendering support, e.g. for emojis). However, the updating sequence needs to be modified, depending on the bar. 
- [ ] In general, sway is tested much better than i3, since it is my daily driver. If you come across any bugs on i3, please open an issue.
- [ ] The unicode PUA (private use area) used contains 137,468 code points, so if you have more than this amount of applications on your system installed, this could be an issue.

## Inspiration

This project is inspired by:
- [i3-workspace-names-daemon](https://github.com/cboddy/i3-workspace-names-daemon)
- [i3scripts/autoname_workspaces.py](https://github.com/justbuchanan/i3scripts)
- [sway-dynamic-names](https://github.com/j-waters/sway-dynamic-names)

Unlike these projects, which rely on pre-existing icon fonts (like FontAwesome or Nerd Fonts) with predefined program-to-icon mappings, my daemon uses actual program icons from your system. Therefore in contrast to these other tools it can 1. show color icons 2.show icons for almost any program automatically. If you encounter a program, where no icon or the wrong icon is shown, please let me know in an issue. 
