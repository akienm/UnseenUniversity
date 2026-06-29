"""
Multi-cloud inference query support (change.40).

Queries several specific models on the same prompt and compares their responses.
This is the *comparison* exception: it names specific models — but, like every
consumer, each query goes through the Inference Proxy (req.model), never a raw
reasoner. Used by the /cloud command in main.py.
"""


def _dispatch(inference, model: str, prompt: str):
    """One Proxy dispatch for `model`. Returns (text, cost). Injectable for tests."""
    if inference is None:
        from unseen_university.devices.inference.device import InferenceDevice

        inference = InferenceDevice()
    from unseen_university.devices.inference.shim import InferenceRequest

    resp = inference.dispatch(
        InferenceRequest(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            task_class="analyst",
            foreground=True,
        )
    )
    return resp.text, resp.cost_estimate


def query_multiple(
    user_input: str,
    models: dict,
    inference=None,
) -> list[tuple[str, str, float]]:
    """
    Send user_input to each named model via the Inference Proxy.
    models: dict of {display_name: model_id}
    Returns list of (display_name, response_text, cost_usd).
    Runs sequentially (not async — safe with all current backends).
    """
    results = []
    for name, model in models.items():
        try:
            text, cost = _dispatch(inference, model, user_input)
            results.append((name, text, cost))
        except Exception as e:
            results.append((name, f"[Error: {e}]", 0.0))
    return results


def compare_responses(responses: list[tuple[str, str, float]], inference=None) -> str:
    """
    Compare multiple model responses, synthesizing via the Inference Proxy.
    The synthesis is operational (asks for a tier, not a model) and routes
    through the Proxy. Falls back to formatted side-by-side if it fails.
    """
    if not responses:
        return "(no responses to compare)"

    lines = [f"Multi-cloud inference comparison ({len(responses)} models):"]
    for name, text, cost in responses:
        cost_str = f"${cost:.4f}" if cost > 0 else "free"
        lines.append(f"\n── {name} ({cost_str}) ──")
        lines.append(text[:500])
    plain = "\n".join(lines)

    # Synthesis via the Proxy (tier-routed; no specific model named).
    try:
        if inference is None:
            from unseen_university.devices.inference.device import InferenceDevice

            inference = InferenceDevice()
        from unseen_university.devices.inference.shim import InferenceRequest

        prompt_parts = [
            "Compare these AI responses to the same question. "
            "Identify: (1) what they agree on, (2) key differences, (3) which is most useful. "
            "Be very brief.\n\n",
        ]
        for name, text, _ in responses:
            prompt_parts.append(f"{name}:\n{text[:300]}\n\n")

        resp = inference.dispatch(
            InferenceRequest(
                messages=[{"role": "user", "content": "".join(prompt_parts)}],
                model="",  # tier-routed synthesis — request no specific model
                task_class="minion",
                max_tokens=200,
                temperature=0.2,
            )
        )
        synthesis = (resp.text or "").strip()
        if not synthesis:
            return plain
        return plain + f"\n\n── Synthesis (via Proxy) ──\n{synthesis}"
    except Exception:
        return plain
