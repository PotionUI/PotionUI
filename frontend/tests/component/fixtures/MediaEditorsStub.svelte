<script lang="ts">
	let {
		request = null,
		onClose,
		onResult
	}: {
		request?: {
			kind: string;
			source: { url: string; fileName: string };
		} | null;
		onClose: () => void;
		onResult: (result: unknown, request: unknown) => void;
	} = $props();
</script>

{#if request}
	<div
		data-editor-stub
		data-kind={request.kind}
		data-url={request.source.url}
	>
		<button type="button" data-stub-close onclick={onClose}>close</button>
		<button
			type="button"
			data-stub-result
			onclick={() =>
				onResult(
					{
						type: 'item',
						item: { id: 'looked', filename: 'looked.png', media_type: 'image', url: '/looked.png' },
						replaced: false
					},
					request
				)}>result</button
		>
	</div>
{/if}
