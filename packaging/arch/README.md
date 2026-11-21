# AUR package: `agentic-hive-git`

`PKGBUILD` builds from the repository's default branch, so the version is the
Nix package version plus the commit count and hash (`0.1.0.r62.g128c33b`).
The unit, sysusers, tmpfiles and profile files beside it are read from the
cloned source, so the AUR repository needs only these files:

```
PKGBUILD
agentic-hive.install
.SRCINFO
```

Publish or update the AUR repository:

```sh
git clone ssh://aur@aur.archlinux.org/agentic-hive-git.git aur
cp PKGBUILD agentic-hive.install aur/
cd aur
makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD agentic-hive.install .SRCINFO
git commit -m "Update"
git push
```

A `-git` package rebuilds from the latest commit whenever the user's AUR helper
checks VCS packages (`yay -Syu --devel`), so new Hive commits do not need a
new AUR push. Push again only when the PKGBUILD itself changes, such as new
dependencies or installed files.

Build locally from this checkout instead of GitHub:

```sh
sed -i "s|^source=.*|source=(\"\$_pkgname::git+file://$PWD/../..\")|" PKGBUILD   # in a copy
makepkg -si
```
