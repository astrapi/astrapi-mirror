"""astrapi_mirror.modules.archlinux._seed – vorkonfigurierte Arch Linux Repos."""


def seed_repos() -> list[dict]:
    """Gibt vorkonfigurierte Arch Linux Repositories zurück."""
    return [
        # ── Offizielle Arch Linux Repos ──────────────────────────────────────
        {
            "label": "Arch Core (Official)",
            "mirror_urls": ["https://mirror.archlinux.org/core/os/x86_64"],
            "enabled": True,
        },
        {
            "label": "Arch Extra (Official)",
            "mirror_urls": ["https://mirror.archlinux.org/extra/os/x86_64"],
            "enabled": True,
        },
        {
            "label": "Arch Multilib (Official)",
            "mirror_urls": ["https://mirror.archlinux.org/multilib/os/x86_64"],
            "enabled": False,
        },
        {
            "label": "Arch Testing (Official)",
            "mirror_urls": ["https://mirror.archlinux.org/testing/os/x86_64"],
            "enabled": False,
        },
        {
            "label": "Arch Community Testing (Official)",
            "mirror_urls": ["https://mirror.archlinux.org/community-testing/os/x86_64"],
            "enabled": False,
        },
        # ── Custom/Third-Party Repos ─────────────────────────────────────────
        {
            "label": "Chaotic AUR",
            "mirror_urls": ["https://lonewolf.piedpiper.com/chaotic-aur/x86_64"],
            "enabled": False,
        },
        {
            "label": "Archzfs",
            "mirror_urls": ["https://archzfs.com/archzfs/x86_64"],
            "enabled": False,
        },
        {
            "label": "BlackArch",
            "mirror_urls": ["https://mirror.blackarch.org/blackarch/os/x86_64"],
            "enabled": False,
        },
        {
            "label": "Docker (Arch)",
            "mirror_urls": ["https://download.docker.com/linux/archlinux/docker-archive"],
            "enabled": False,
        },
        {
            "label": "KDE Unstable",
            "mirror_urls": ["https://mirror.archlinux.org/kde-unstable/os/x86_64"],
            "enabled": False,
        },
        {
            "label": "GNOME Unstable",
            "mirror_urls": ["https://mirror.archlinux.org/gnome-unstable/os/x86_64"],
            "enabled": False,
        },
        {
            "label": "Arch Linux ARM (aarch64)",
            "mirror_urls": ["http://mirror.archlinuxarm.org/aarch64/core"],
            "enabled": False,
        },
        {
            "label": "Arch Linux ARM (armv7h)",
            "mirror_urls": ["http://mirror.archlinuxarm.org/armv7h/core"],
            "enabled": False,
        },
    ]


def auto_seed(store) -> None:
    """Spielt Repos in den Store ein, falls dieser noch leer ist."""
    existing = store.list()
    if existing:
        return

    for repo_data in seed_repos():
        store.create("", repo_data)
