<script lang="ts">
	const fail = () => {
		throw new Error('options exploded');
	};
	fail();
</script>

<div data-plugin-options>never shown</div>
