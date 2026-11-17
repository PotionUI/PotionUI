<script lang="ts">
	import EditStack from '$lib/components/tool-tabs/frame/EditStack.svelte';
	import PaintEditsGroup from '$lib/components/paint/PaintEditsGroup.svelte';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';
	import type { EditStackState } from '$lib/tool-tabs/frame/editStack';
	import type { StackProgress, StackVariant } from '$lib/tool-tabs/frame/types';

	let {
		stack,
		session,
		state: snapshot,
		progress = null,
		variant = 'panel',
		phone = false
	}: {
		stack: EditStackState<unknown>;
		session: PaintSession;
		state: SessionSnapshot;
		progress?: StackProgress | null;
		variant?: StackVariant;
		phone?: boolean;
	} = $props();

	const none = () => undefined;
</script>

<EditStack {stack} original={{ summary: '1024×1024' }} {variant} {progress} onreopen={none} onseek={none} ontogglehidden={none} onremove={none} onmove={none}>
	{#snippet progressRow(index)}
		<PaintEditsGroup {session} state={snapshot} {phone} {index} />
	{/snippet}
</EditStack>
