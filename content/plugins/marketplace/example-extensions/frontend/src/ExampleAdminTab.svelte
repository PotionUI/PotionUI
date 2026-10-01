<script>
	import HostUi from '../../../../sdk/HostUi.svelte';

	let clicks = $state(0);
</script>

<div class="p-6 space-y-4" data-testid="example-host-ui">
	<h2 class="text-lg font-semibold tracking-[0.37em]" data-testid="example-host-ui-title">Example Extension</h2>
	<p class="text-fg-muted">
		This tab is contributed by the <code>example-extensions</code> plugin via the <code>admin.tabs</code> extension slot.
	</p>
	<div class="flex items-center gap-3">
		<HostUi name="Button" props={{ variant: 'primary', onclick: () => (clicks += 1) }}>Host button</HostUi>
		<HostUi name="Badge">Host badge</HostUi>
		<HostUi name="Tooltip" props={{ text: 'Rendered by the host Tooltip' }}>
			<span class="text-fg-muted underline decoration-dotted">Hover me</span>
		</HostUi>
		<span class="font-mono tabular-nums text-fg-muted" data-testid="example-host-ui-clicks">{clicks}</span>
	</div>
</div>
