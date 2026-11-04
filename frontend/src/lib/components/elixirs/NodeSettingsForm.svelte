<script lang="ts">
	import { Switch } from '$lib/components/ui';
	import DynamicForm from '$lib/components/DynamicForm.svelte';
	import { untrack } from 'svelte';
	import { formOverrides } from '$lib/elixirs/model';
	import type { ElixirNode, NodeOverrides } from '$lib/elixirs/types';

	interface Props {
		node: ElixirNode;
		onchange: (overrides: NodeOverrides) => void;
	}

	let { node, onchange }: Props = $props();

	const initialData = untrack(() => ({
		...(node.session_snapshot?.formData || {}),
		...(node.overrides?.formData || {})
	}));
	const variant = untrack(() => node.form_name || node.session_snapshot?.selectedVariant || undefined);

	let advanced = $state(false);
	let baseline: Record<string, any> | null = null;
	let latest = untrack(() => node);

	$effect(() => {
		latest = node;
	});

	function published(data: Record<string, any>) {
		if (baseline === null) {
			baseline = data;
			return;
		}
		const next = formOverrides(latest.overrides, latest.session_snapshot?.formData, baseline, data);
		baseline = data;
		if (JSON.stringify(next) !== JSON.stringify(latest.overrides ?? {})) onchange(next);
	}

	const presetId = untrack(() => node.preset_id);
	const mode = untrack(() => node.mode);

	let formProps = $derived({
		presetId: presetId as string,
		mode: mode as string,
		variant,
		formName: 'Node settings',
		initialData,
		audience: advanced ? ('advanced' as const) : ('simple' as const),
		onFormDataChange: published
	});
</script>

<div class="flex flex-col gap-3">
	<div class="flex items-center justify-between gap-2">
		<span class="text-sm text-fg-muted">Show advanced settings</span>
		<Switch checked={advanced} label="Show advanced settings" onchange={(value) => (advanced = value)} />
	</div>
	<DynamicForm {...formProps} />
</div>
