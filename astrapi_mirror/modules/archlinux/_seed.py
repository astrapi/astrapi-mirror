"""astrapi_mirror.modules.archlinux._seed – vorkonfigurierte Arch Linux Repos."""


def seed_repos() -> list[dict]:
    """Gibt 15 vorkonfigurierte Arch Linux Repositories zurück."""
    return [
        # ── Offizielle Arch Linux Repos ──────────────────────────────────────
        {
            "label": "Arch Core (Official)",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/core/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": True,
        },
        {
            "label": "Arch Extra (Official)",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/extra/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": True,
        },
        {
            "label": "Arch Community (Official)",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/community/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": True,
        },
        {
            "label": "Arch Testing (Official)",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/testing/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,  # standardmäßig deaktiviert
        },
        {
            "label": "Arch Community Testing (Official)",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/community-testing/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,  # standardmäßig deaktiviert
        },
        # ── Custom/Third-Party Repos ─────────────────────────────────────────
        {
            "label": "Chaotic AUR",
            "url": "https://lonewolf.piedpiper.com/chaotic-aur/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "Archzfs",
            "url": "https://archzfs.com/archzfs/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "BlackArch",
            "url": "https://mirror.blackarch.org/blackarch/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "PostgreSQL",
            "url": "https://repo.postgresqlfr.org/debian/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "Docker",
            "url": "https://download.docker.com/linux/archlinux/docker-archive/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "Multilib (für 32-bit support)",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/multilib/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "KDE Unstable",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/kde-unstable/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "GNOME Unstable",
            "url": "https://mirror.archlinux.org/iso/latest/arch/repos/gnome-unstable/os/x86_64/",
            "architectures": ["x86_64"],
            "enabled": False,
        },
        {
            "label": "Arch Linux ARM",
            "url": "http://mirror.archlinuxarm.org/",
            "architectures": ["aarch64", "armv7h"],
            "enabled": False,
        },
        {
            "label": "Endeavour OS (AUR-like)",
            "url": "https://github.com/endeavouros-team/",
            "architectures": ["x86_64"],
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
