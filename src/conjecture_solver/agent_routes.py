"""Resolve reviewer settings without borrowing another backend's connection."""


def review_route(
    backend, executable, provider_config, *,
    judge_backend=None, judge_executable=None, judge_provider_config=None,
):
    selected = judge_backend or backend
    same_backend = selected == backend
    return dict(
        backend=selected,
        executable=judge_executable or (executable if same_backend else selected),
        provider_config=judge_provider_config or (provider_config if same_backend else None),
    )
