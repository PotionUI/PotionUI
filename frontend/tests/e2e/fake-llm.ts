import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { expect, type APIRequestContext } from '@playwright/test';

export type FakeTurn =
	| { kind: 'text'; text: string }
	| { kind: 'tool_call'; name: string; arguments: Record<string, unknown> };

export interface CapturedRequest {
	stream: boolean;
	toolCount: number;
	messages: Array<{ role: string; content: unknown }>;
	servedAs: 'queued-turn' | 'title' | 'empty-queue';
}

export interface FakeLLMServer {
	/** OpenAI-compatible base_url (ends in /v1). */
	url: string;
	requests: CapturedRequest[];
	enqueue(...turns: FakeTurn[]): void;
	pending(): number;
	close(): Promise<void>;
}

const TITLE_MARKER = 'Write a 3-6 word title';

function readBody(req: IncomingMessage): Promise<string> {
	return new Promise((resolve, reject) => {
		const chunks: Buffer[] = [];
		req.on('data', (c) => chunks.push(c));
		req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
		req.on('error', reject);
	});
}

const USAGE = { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 };

function completionJson(turn: FakeTurn): string {
	const message =
		turn.kind === 'text'
			? { role: 'assistant', content: turn.text }
			: {
					role: 'assistant',
					content: null,
					tool_calls: [
						{
							id: 'call_e2e_0',
							type: 'function',
							function: { name: turn.name, arguments: JSON.stringify(turn.arguments) }
						}
					]
				};
	return JSON.stringify({
		id: 'chatcmpl-e2e',
		object: 'chat.completion',
		model: 'fake',
		choices: [
			{
				index: 0,
				message,
				finish_reason: turn.kind === 'text' ? 'stop' : 'tool_calls'
			}
		],
		usage: USAGE
	});
}

function sseChunk(payload: Record<string, unknown>): string {
	return `data: ${JSON.stringify({ id: 'chatcmpl-e2e', object: 'chat.completion.chunk', model: 'fake', ...payload })}\n\n`;
}

function writeStreamed(res: ServerResponse, turn: FakeTurn): void {
	res.writeHead(200, {
		'Content-Type': 'text/event-stream',
		'Cache-Control': 'no-cache',
		Connection: 'keep-alive'
	});

	if (turn.kind === 'text') {
		// Token-by-token so the frontend's streaming accumulation path is exercised.
		for (const token of turn.text.match(/\S+\s*/g) || ['']) {
			res.write(sseChunk({ choices: [{ index: 0, delta: { content: token }, finish_reason: null }] }));
		}
		res.write(sseChunk({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }));
	} else {
		// OpenAI streamed tool_calls contract (assembled in
		// src/features/llm/clients/openai.py): first fragment per index carries
		// id/type/function.name, later fragments only append to function.arguments.
		res.write(
			sseChunk({
				choices: [
					{
						index: 0,
						delta: {
							tool_calls: [
								{
									index: 0,
									id: 'call_e2e_0',
									type: 'function',
									function: { name: turn.name, arguments: '' }
								}
							]
						},
						finish_reason: null
					}
				]
			})
		);
		const args = JSON.stringify(turn.arguments);
		const mid = Math.ceil(args.length / 2);
		for (const part of [args.slice(0, mid), args.slice(mid)]) {
			if (!part) continue;
			res.write(
				sseChunk({
					choices: [
						{
							index: 0,
							delta: { tool_calls: [{ index: 0, function: { arguments: part } }] },
							finish_reason: null
						}
					]
				})
			);
		}
		res.write(sseChunk({ choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] }));
	}

	res.write(sseChunk({ choices: [], usage: USAGE }));
	res.write('data: [DONE]\n\n');
	res.end();
}

export async function startFakeLLM(): Promise<FakeLLMServer> {
	const queue: FakeTurn[] = [];
	const requests: CapturedRequest[] = [];

	const server: Server = createServer(async (req, res) => {
		if (req.method !== 'POST' || !(req.url || '').endsWith('/chat/completions')) {
			res.writeHead(404, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ error: 'not found' }));
			return;
		}

		let body: any = {};
		try {
			body = JSON.parse(await readBody(req));
		} catch {
			/* keep {} */
		}
		const messages: Array<{ role: string; content: unknown }> = body.messages || [];
		const stream = body.stream === true;

		// Session-title generation fires as a side request after the first
		// exchange; answer it canned so it never consumes a scripted turn.
		const isTitle = messages.some(
			(m) => typeof m.content === 'string' && m.content.includes(TITLE_MARKER)
		);

		let turn: FakeTurn;
		let servedAs: CapturedRequest['servedAs'];
		if (isTitle) {
			turn = { kind: 'text', text: 'E2E Fake Chat' };
			servedAs = 'title';
		} else if (queue.length > 0) {
			turn = queue.shift()!;
			servedAs = 'queued-turn';
		} else {
			turn = { kind: 'text', text: '(fake-llm: no scripted turn left)' };
			servedAs = 'empty-queue';
		}
		requests.push({ stream, toolCount: (body.tools || []).length, messages, servedAs });

		if (stream) {
			writeStreamed(res, turn);
		} else {
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(completionJson(turn));
		}
	});

	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	const address = server.address();
	if (address === null || typeof address === 'string') {
		throw new Error('fake-llm server did not bind to a port');
	}

	return {
		url: `http://127.0.0.1:${address.port}/v1`,
		requests,
		enqueue: (...turns: FakeTurn[]) => queue.push(...turns),
		pending: () => queue.length,
		close: () => new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve())))
	};
}

const SEED_CONFIG_NAME = 'e2e-fake-openai';

/**
 * Seed the throwaway backend with an enabled OpenAI-type LLM configuration
 * pointing at the fake server, make it the default, and assign it to the
 * owner (the chat UI only lists configs from /api/llm/configurations/my).
 *
 * Upserts by name: when several specs run against one shared backend, each
 * test's fake server binds a new ephemeral port, and the chat UI falls back
 * to the FIRST listed config — a stale second config would point at a dead
 * port.
 */
export async function seedFakeLlmConfig(
	request: APIRequestContext,
	backendUrl: string,
	token: string,
	fakeBaseUrl: string
): Promise<string> {
	const headers = { Authorization: `Bearer ${token}` };

	const configPayload = {
		name: SEED_CONFIG_NAME,
		type: 'openai',
		enabled: true,
		base_url: fakeBaseUrl,
		model: 'fake',
		system_message: 'You are a test assistant.',
		temperature: 0.2,
		max_tokens: 512,
		timeout: 30
	};

	const listing = await request.get(`${backendUrl}/api/llm/configurations`, { headers });
	expect(listing.ok(), `LLM config list -> ${listing.status()}`).toBeTruthy();
	const listingJson = await listing.json();
	const configs: any[] = listingJson.data?.configurations || listingJson.data || [];
	const existing = Array.isArray(configs)
		? configs.find((c: any) => c?.name === SEED_CONFIG_NAME)
		: undefined;

	let configId: string;
	if (existing?.id) {
		configId = existing.id;
		const updated = await request.put(`${backendUrl}/api/llm/configurations/${configId}`, {
			headers,
			data: { id: configId, ...configPayload }
		});
		expect(updated.ok(), `LLM config update -> ${updated.status()}`).toBeTruthy();
		const updatedJson = await updated.json();
		expect(updatedJson.success, `LLM config update: ${JSON.stringify(updatedJson)}`).toBeTruthy();
	} else {
		const created = await request.post(`${backendUrl}/api/llm/configurations`, {
			headers,
			data: configPayload
		});
		expect(created.ok(), `LLM config create -> ${created.status()}`).toBeTruthy();
		const createdJson = await created.json();
		expect(createdJson.success, `LLM config create: ${JSON.stringify(createdJson)}`).toBeTruthy();
		configId = createdJson.data?.id as string;
	}
	expect(configId, 'LLM config id').toBeTruthy();

	const setDefault = await request.post(
		`${backendUrl}/api/llm/configurations/${configId}/set-default`,
		{ headers }
	);
	expect(setDefault.ok(), `set-default -> ${setDefault.status()}`).toBeTruthy();

	const me = await request.get(`${backendUrl}/api/auth/me`, { headers });
	expect(me.ok(), `auth/me -> ${me.status()}`).toBeTruthy();
	const userId = (await me.json())?.data?.id as string;
	expect(userId, 'owner user id from /api/auth/me').toBeTruthy();

	const myAssignments = await request.get(
		`${backendUrl}/api/llm/user-assignments/${userId}`,
		{ headers }
	);
	const myAssignmentsText = myAssignments.ok()
		? JSON.stringify(await myAssignments.json())
		: '';
	if (!myAssignmentsText.includes(configId)) {
		const assigned = await request.post(`${backendUrl}/api/llm/user-assignments`, {
			headers,
			data: { user_id: userId, llm_config_id: configId }
		});
		expect(assigned.ok(), `user-assignment -> ${assigned.status()}`).toBeTruthy();
		const assignedJson = await assigned.json();
		expect(assignedJson.success, `user-assignment: ${JSON.stringify(assignedJson)}`).toBeTruthy();
	}

	return configId;
}
