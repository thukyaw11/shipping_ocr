interface Env {
	AI: Ai;
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		if (request.method === "OPTIONS") {
			return new Response(null, { headers: corsHeaders() });
		}

		if (request.method !== "POST") {
			return new Response("Method not allowed", { status: 405 });
		}

		const url = new URL(request.url);
		if (url.pathname !== "/v1/chat/completions") {
			return new Response("Not found", { status: 404 });
		}

		const body = (await request.json()) as {
			model: string;
			messages: { role: string; content: string }[];
			response_format?: { type: string; json_schema?: { name: string; schema: unknown } };
		};

		const { model, messages, response_format } = body;

		const aiOptions: Record<string, unknown> = { messages };
		if (response_format) {
			aiOptions.response_format = response_format;
		}

		const result = (await env.AI.run(model as BaseAiTextGenerationModels, aiOptions as AiTextGenerationInput)) as {
			response?: string;
		};

		const content = result.response ?? "";

		return Response.json(
			{
				id: `cf-${Date.now()}`,
				object: "chat.completion",
				choices: [
					{
						index: 0,
						message: { role: "assistant", content },
						finish_reason: "stop",
					},
				],
			},
			{ headers: corsHeaders() }
		);
	},
} satisfies ExportedHandler<Env>;

function corsHeaders(): HeadersInit {
	return {
		"Access-Control-Allow-Origin": "*",
		"Access-Control-Allow-Methods": "POST, OPTIONS",
		"Access-Control-Allow-Headers": "Content-Type, Authorization",
		"Content-Type": "application/json",
	};
}
