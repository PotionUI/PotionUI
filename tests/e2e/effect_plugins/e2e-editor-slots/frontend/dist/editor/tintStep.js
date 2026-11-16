export default {
	version: 1,
	summary: (params) => `Strength ${params.amount}`,
	payload: (params, ctx) => [{ tool: ctx.step.tool, params: { amount: params.amount } }]
};
