import { registerFieldComponent } from './registry';
import TextInput from '$lib/components/form-fields/TextInput.svelte';
import NumberInput from '$lib/components/form-fields/NumberInput.svelte';
import SliderField from '$lib/components/form-fields/SliderField.svelte';
import SeedField from '$lib/components/form-fields/SeedField.svelte';
import CheckboxField from '$lib/components/form-fields/CheckboxField.svelte';
import SelectField from '$lib/components/form-fields/SelectField.svelte';
import RowField from '$lib/components/form-fields/RowField.svelte';
import SegmentedField from '$lib/components/form-fields/SegmentedField.svelte';
import AnchorField from '$lib/components/form-fields/AnchorField.svelte';
import ColorField from '$lib/components/form-fields/ColorField.svelte';
import NumberBoxField from '$lib/components/form-fields/NumberBoxField.svelte';

const COMMITS_OWN = new Set(['string', 'textbox', 'number', 'integer', 'stepper', 'slider', 'boolean', 'checkbox', 'select', 'segmented', 'number_box']);

const TABLE: Record<string, unknown> = {
	string: TextInput,
	textbox: TextInput,
	number: NumberInput,
	integer: NumberInput,
	stepper: NumberInput,
	slider: SliderField,
	seed: SeedField,
	boolean: CheckboxField,
	checkbox: CheckboxField,
	select: SelectField,
	row: RowField,
	segmented: SegmentedField,
	anchor: AnchorField,
	color: ColorField,
	number_box: NumberBoxField
};

let registered = false;

export function registerEditorFieldComponents(): void {
	if (registered) return;
	registered = true;
	for (const [type, component] of Object.entries(TABLE)) {
		registerFieldComponent(type, { component, commits: COMMITS_OWN.has(type) });
	}
}
