<script>
	/**
	 * Reference `admin.tabs` extension slot contribution (A5). Mounted by
	 * `routes/admin/+page.svelte` with no props when the plugin's tab is active.
	 */
</script>

<div class="example-admin-tab">
	<h2>Example Extension</h2>
	<p>This tab is contributed by the <code>example-extensions</code> plugin via the <code>admin.tabs</code> extension slot.</p>
</div>

<style>
	.example-admin-tab {
		padding: 1.5rem;
	}
	h2 {
		font-size: 1.125rem;
		font-weight: 600;
		margin-bottom: 0.5rem;
	}
</style>
