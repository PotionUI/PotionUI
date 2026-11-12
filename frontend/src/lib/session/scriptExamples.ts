export interface ScriptField {
	name: string;
	label: string;
	type: string;
	kind: string;
	value?: unknown;
	locked: boolean;
	media: boolean;
}

export interface ScriptProblem {
	code: string;
	message: string;
}

export interface ScriptTemplate {
	session: {
		id: string;
		name: string;
		preset_id: string;
		preset_name: string | null;
		modes: string[];
		mode: string;
		settings_hash: string;
	};
	prompt: { positive: string; negative: string; batch: number };
	shortcuts: { seed: string | null; count: string | null };
	fields: ScriptField[];
	problems: ScriptProblem[];
	editor_only: { supported: string; message: string } | null;
}

export type ExampleKind = 'curl' | 'python' | 'javascript' | 'mcp';

export interface ExampleOptions {
	base: string;
	pinned: boolean;
}

export const KEY_PLACEHOLDER = '$POTIONUI_KEY';
export const SAMPLE_PROMPT = 'your new prompt';

function body(template: ScriptTemplate, pinned: boolean): Record<string, unknown> {
	const { session } = template;
	return {
		...(session.modes.length > 1 ? { mode: session.mode } : {}),
		overrides: { prompt: SAMPLE_PROMPT },
		...(pinned ? { if_settings_hash: session.settings_hash } : {})
	};
}

function mcpArguments(template: ScriptTemplate): Record<string, unknown> {
	const { session } = template;
	return {
		session_id: session.id,
		...(session.modes.length > 1 ? { mode: session.mode } : {}),
		prompt: SAMPLE_PROMPT
	};
}

export function buildExamples(template: ScriptTemplate, options: ExampleOptions): Record<ExampleKind, string> {
	const url = `${options.base}/api/sessions/${template.session.id}/generate`;
	const payload = body(template, options.pinned);
	const json = JSON.stringify(payload, null, 2);
	const compact = JSON.stringify(payload);
	const curl = [
		`curl -X POST "${url}" \\`,
		`  -H "Authorization: Bearer ${KEY_PLACEHOLDER}" \\`,
		`  -H "Content-Type: application/json" \\`,
		`  -d '${compact}'`
	].join('\n');
	const python = [
		'import os, requests',
		'',
		'response = requests.post(',
		`    "${url}",`,
		`    headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]},`,
		`    json=${json.replace(/\n/g, '\n    ')},`,
		')',
		'print(response.json()["data"]["generation_id"])'
	].join('\n');
	const javascript = [
		`const response = await fetch("${url}", {`,
		'  method: "POST",',
		'  headers: {',
		'    Authorization: `Bearer ${process.env.POTIONUI_KEY}`,',
		'    "Content-Type": "application/json"',
		'  },',
		`  body: JSON.stringify(${json.replace(/\n/g, '\n  ')})`,
		'});',
		'console.log((await response.json()).data.generation_id);'
	].join('\n');
	const mcp = JSON.stringify(
		{
			jsonrpc: '2.0',
			id: 1,
			method: 'tools/call',
			params: { name: 'start_generation', arguments: mcpArguments(template) }
		},
		null,
		2
	);
	return { curl, python, javascript, mcp };
}

export function changeableFields(template: ScriptTemplate): ScriptField[] {
	return template.fields.filter((field) => field.kind !== 'display');
}
