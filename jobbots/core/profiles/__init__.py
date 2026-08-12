"""jobbots.core.profiles — layered profile/configuration loading + runtime activation."""
from jobbots.core.profiles.loader import (  # noqa: F401
    Profile,
    available_profiles,
    load_profile,
    profile_env,
    resolve_secret,
)
from jobbots.core.profiles.runtime import (  # noqa: F401
    IDENTITY_KEYS,
    activate_profile,
    assert_manifest_matches_registry,
    bot_env,
)
