<script lang="ts">
	export let context: Record<string, unknown> = {};
	export let hookName: string = '';
	export let pluginId: string = '';
</script>

<span class="stub-plugin-component" data-stub-plugin={pluginId} data-stub-hook={hookName}
	>{Object.keys(context).length}</span
>
