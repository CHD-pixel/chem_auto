# Artifact naming constants and template helpers.
#
# ADK user-scoped artifacts use the "user:" filename prefix. Both
# InMemoryArtifactService and FileArtifactService recognise this prefix
# and store the artifact at the user level (not session level), making
# it accessible from any session.

# Published registry (user-scoped via "user:" prefix)
REGISTRY_ARTIFACT = "user:user_registry/instruments.json"


def manual_artifact_name(manual_id: str) -> str:
    return f"session_manuals/{manual_id}.pdf"


def candidate_driver_artifact(device_id: str, round_num: int | None = None) -> str:
    if round_num is not None:
        return f"session_candidates/{device_id}/candidate_driver_round_{round_num}.py"
    return f"session_candidates/{device_id}/candidate_driver.py"


def log_artifact(run_id: str, function_name: str, stream: str) -> str:
    return f"session_logs/{run_id}/{function_name}.{stream}.txt"


def user_confirmation_artifact(run_id: str, function_name: str) -> str:
    return f"session_logs/{run_id}/{function_name}.user_confirmation.txt"


def published_driver_artifact(device_id: str, version: str) -> str:
    return f"user:user_drivers/{device_id}/{version}/driver.py"


def published_manifest_artifact(device_id: str, version: str) -> str:
    return f"user:user_drivers/{device_id}/{version}/manifest.json"


def published_safety_artifact(device_id: str, version: str) -> str:
    return f"user:user_drivers/{device_id}/{version}/safety.json"


def published_function_catalog_artifact(device_id: str, version: str) -> str:
    return f"user:user_drivers/{device_id}/{version}/function_catalog.json"


def published_build_blueprint_artifact(device_id: str, version: str) -> str:
    return f"user:user_drivers/{device_id}/{version}/build_blueprint.json"


# Experiment plans (user-scoped via "user:" prefix)
EXPERIMENT_PLANS_INDEX = "user:user_experiments/plans/index.json"


def experiment_plan_artifact(plan_id: str) -> str:
    return f"user:user_experiments/plans/{plan_id}.json"


# Experiment logs (user-scoped via "user:" prefix)
EXPERIMENT_LOGS_INDEX = "user:user_experiments/logs/index.json"


def experiment_log_artifact(log_id: str) -> str:
    return f"user:user_experiments/logs/{log_id}.json"
