"""Start the independent PA writer without blocking retrieval or the main reply."""
from __future__ import annotations


async def prepare_pa_reply_node(state, runtime):
    from .pa_card_tools import enabled, form_ui_enabled
    from .pa_background import schedule_pa_background

    context = runtime.context
    module = state.get('extracted_intent') or state.get('current_module')
    eligible = ((module == 'module_2' and form_ui_enabled(context.settings, state))
                or (module in {'module_2', 'module_4'} and enabled(context.settings, state)))
    if not eligible:
        return {}
    pending = {**state, 'pa_background_pending': True}
    task = schedule_pa_background(pending, context, assistant_message_id=None, parallel=True)
    if task is None:
        return {}
    # The foreground must never persist its earlier runtime snapshot over a
    # concurrent card commit. The HTTP finalizer reloads committed state.
    context.pa_tools_started = True
    return {'pa_background_pending': True, 'pa_operations_completed': False,
            'telemetry': {**(state.get('telemetry') or {}),
                'pa_background_tools': {'status': 'running_in_parallel',
                                       'timing': 'parallel_with_main_reply'}}}
