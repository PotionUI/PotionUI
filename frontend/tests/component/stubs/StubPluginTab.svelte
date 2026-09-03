<script lang="ts">
	// Stand-in for a plugin's own `admin_tabs` component - just proves the
	// resolver-mounted component actually received the props PluginsTab
	// promises (`pluginId`, `plugin`). See pluginDetailAdminTabs.test.ts.
	export let pluginId: string;
	export let plugin: { name?: string } | undefined = undefined;
</script>

<div data-testid="stub-plugin-tab">Stub tab for {pluginId} ({plugin?.name ?? 'unknown'})</div>
