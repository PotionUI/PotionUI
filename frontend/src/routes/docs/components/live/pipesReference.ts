export interface PipeInputSpec {
	name: string;
	io_type: string;
	required?: boolean;
	description?: string;
	is_array?: boolean;
	[key: string]: unknown;
}

export interface PipeOutputSpec {
	name: string;
	io_type: string;
	description?: string;
	is_array?: boolean;
	[key: string]: unknown;
}

export interface PipeConfigSpec {
	name: string;
	param_type?: string;
	default?: unknown;
	description?: string;
	required?: boolean;
	choices?: unknown[] | null;
	min_value?: number | null;
	max_value?: number | null;
	[key: string]: unknown;
}

export interface PipeEntry {
	name?: string;
	id?: string;
	description?: string;
	status?: string;
	manual_install?: string | null;
	requirements?: unknown;
	inputs?: PipeInputSpec[];
	outputs?: PipeOutputSpec[];
	configuration?: PipeConfigSpec[];
	[key: string]: unknown;
}

export function pipeKey(pipe: PipeEntry, index: number): string {
	return String(pipe.id ?? pipe.name ?? index);
}

export function pipeLabel(pipe: PipeEntry): string {
	return String(pipe.name ?? pipe.id ?? 'unknown');
}

/** First paragraph only, folded to one line - a defensive mirror of the
 * backend documenter's own trim, since this API is loosely typed and a
 * plugin pipe could still hand back a raw multi-paragraph docstring. */
export function pipeDescription(pipe: PipeEntry): string {
	const raw = pipe.description ?? '';
	const firstParagraph = raw.split(/\n\s*\n/)[0] ?? '';
	return firstParagraph
		.split('\n')
		.map((line) => line.trim())
		.join(' ')
		.trim();
}

function humanize(segment: string): string {
	return segment
		.split('_')
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

/** Pipe names are laid out as "<family>/<variant>" (e.g. "generator/z_image"),
 * mirroring their location under src/pipelines/pipes/ - not every pipe has a
 * variant (a flat pipe like "audio_trim" has none). */
export function pipeFamily(pipe: PipeEntry): string | undefined {
	const name = pipeLabel(pipe);
	const slash = name.indexOf('/');
	return slash === -1 ? undefined : name.slice(0, slash);
}

export function pipeVariant(pipe: PipeEntry): string | undefined {
	const name = pipeLabel(pipe);
	const slash = name.indexOf('/');
	return slash === -1 ? undefined : name.slice(slash + 1);
}

export function pipeFamilyLabel(pipe: PipeEntry): string | undefined {
	const family = pipeFamily(pipe);
	if (!family) return undefined;
	const variant = pipeVariant(pipe);
	return variant ? `${humanize(family)} · ${humanize(variant)}` : humanize(family);
}

/** Humanized variant alone (no family prefix) - for a card header where the
 * family is already the enclosing section's heading. */
export function pipeVariantLabel(pipe: PipeEntry): string | undefined {
	const variant = pipeVariant(pipe);
	return variant ? humanize(variant) : undefined;
}

export interface PipeFamilyGroup {
	family: string;
	pipes: PipeEntry[];
}

/** The grouping key used to section the reference: the family segment, or
 * the pipe's own full name for a flat pipe (it becomes a singleton group). */
export function pipeGroupFamily(pipe: PipeEntry): string {
	return pipeFamily(pipe) ?? pipeLabel(pipe);
}

/** Sections pipes by family, families alphabetical, pipes within a family
 * alphabetical by name. */
export function groupPipesByFamily(pipes: PipeEntry[]): PipeFamilyGroup[] {
	const byFamily = new Map<string, PipeEntry[]>();
	for (const pipe of pipes) {
		const family = pipeGroupFamily(pipe);
		const existing = byFamily.get(family);
		if (existing) {
			existing.push(pipe);
		} else {
			byFamily.set(family, [pipe]);
		}
	}
	return [...byFamily.entries()]
		.sort(([a], [b]) => a.localeCompare(b))
		.map(([family, familyPipes]) => ({
			family,
			pipes: [...familyPipes].sort((a, b) => pipeLabel(a).localeCompare(pipeLabel(b)))
		}));
}

/** Stable anchor id for a family's section heading, used by the jump index. */
export function pipeFamilyAnchor(family: string): string {
	const slug = family
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '');
	return `pipe-family-${slug || 'unknown'}`;
}

export function pipeInputs(pipe: PipeEntry): PipeInputSpec[] {
	return pipe.inputs ?? [];
}

export function pipeOutputs(pipe: PipeEntry): PipeOutputSpec[] {
	return pipe.outputs ?? [];
}

export function pipeConfiguration(pipe: PipeEntry): PipeConfigSpec[] {
	return pipe.configuration ?? [];
}

export function matchesPipe(pipe: PipeEntry, query: string): boolean {
	const needle = query.toLowerCase();
	if (pipeLabel(pipe).toLowerCase().includes(needle)) return true;
	if (pipeDescription(pipe).toLowerCase().includes(needle)) return true;
	const family = pipeFamily(pipe);
	if (family && family.toLowerCase().includes(needle)) return true;
	const ioMatches = (io: { name: string; io_type: string }) =>
		io.name.toLowerCase().includes(needle) || io.io_type.toLowerCase().includes(needle);
	if (pipeInputs(pipe).some(ioMatches)) return true;
	return pipeOutputs(pipe).some(ioMatches);
}

/** Label rendered on an input/output chip: the name, `[]`-suffixed when the
 * slot is an array. */
export function ioChipLabel(io: { name: string; is_array?: boolean }): string {
	return io.is_array ? `${io.name}[]` : io.name;
}

/** Tooltip text for an input/output chip - its description, with a quiet
 * "required" note appended for required inputs. */
export function ioChipTitle(io: { description?: string }, required?: boolean): string | undefined {
	const parts: string[] = [];
	if (io.description) parts.push(io.description);
	if (required) parts.push('required');
	return parts.length > 0 ? parts.join(' — ') : undefined;
}
