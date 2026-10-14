# Security

godot-gulp reads untrusted files: asset packs from stores and archives. It parses them with OpenUSD and Pillow,
writes only under the output folder you give it, and runs no code from the pack. The optional import check runs
Godot on the files it wrote, in a temporary stub project.

Please report a vulnerability (a file that makes godot-gulp write outside its output folder, run code, or exhaust
memory or disk) privately, through GitHub's private vulnerability reporting on this repository, not in a public
issue. Include a minimal synthetic reproduction if you can.
