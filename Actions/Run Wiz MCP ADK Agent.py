from SiemplifyAction import SiemplifyAction
from SiemplifyUtils import output_handler
from ScriptResult import EXECUTION_STATE_COMPLETED, EXECUTION_STATE_FAILED
from GoogleADKManager import GoogleADKManager

INTEGRATION_NAME = "Google ADK"
SCRIPT_NAME = "Run Wiz MCP ADK Agent"
DEFAULT_WIZ_MCP_URL = "https://mcp.app.wiz.io"

@output_handler
def main():
    siemplify = SiemplifyAction()
    siemplify.script_name = SCRIPT_NAME

    # Initialize default states
    status = EXECUTION_STATE_COMPLETED
    output_message = ""
    result_value = False

    try:
        # 1. Fetch Global Configuration (Integration Level)
        api_key = siemplify.extract_configuration_param(INTEGRATION_NAME, "Gemini API Key")
        sa_json = siemplify.extract_configuration_param(INTEGRATION_NAME, "Service Account JSON")
        model_name = siemplify.extract_configuration_param(INTEGRATION_NAME, "Model Name", default_value="gemini-3.7-flash")
        region = siemplify.extract_configuration_param(INTEGRATION_NAME, "SecOps Region")
        agent_engine_resource = siemplify.extract_configuration_param(INTEGRATION_NAME, "Agent Engine Resource Name")
        proj_id = siemplify.extract_configuration_param(INTEGRATION_NAME, "GCP Project ID")

        # Wiz MCP Integration service account credentials
        wiz_mcp_url = siemplify.extract_configuration_param(INTEGRATION_NAME, "Wiz MCP Server URL") or DEFAULT_WIZ_MCP_URL
        wiz_client_id = siemplify.extract_configuration_param(INTEGRATION_NAME, "Wiz Client ID")
        wiz_client_secret = siemplify.extract_configuration_param(INTEGRATION_NAME, "Wiz Client Secret")
        wiz_data_center = siemplify.extract_configuration_param(INTEGRATION_NAME, "Wiz Data Center")

        # 2. Fetch Action-Specific Parameters
        user_prompt = siemplify.extract_action_param("User Prompt")
        raw_budget = siemplify.extract_action_param("Thinking Budget", default_value="0")
        try:
            thinking_budget = int(raw_budget) if raw_budget else 0
            if thinking_budget < 0:
                raise ValueError()
        except ValueError:
            raise ValueError(f"'Thinking Budget' must be a non-negative integer, got: '{raw_budget}'")
        wiz_domains_raw = siemplify.extract_action_param("Wiz Domains")
        enable_memory = siemplify.extract_action_param("Enable Memory", input_type=bool, default_value=False)
        raw_session_id = siemplify.extract_action_param("Session ID")

        wiz_domains = []
        if wiz_domains_raw and str(wiz_domains_raw).strip():
            wiz_domains = [d.strip().strip("'").strip('"').strip() for d in str(wiz_domains_raw).split(",") if d.strip()]

        # 3. Manager Setup (Gemini/Vertex credentials drive the model; Wiz credentials only drive the MCP connection)
        manager = GoogleADKManager(
            api_key=api_key,
            service_account_json=sa_json,
            model_name=model_name,
            logger=siemplify.LOGGER,
            agent_engine_resource_name=agent_engine_resource,
            project_id=proj_id,
            location=region
        )

        # 4. Resolve Memory Configuration
        session_id, memory_service, memory_tools = manager.resolve_memory_configuration(
            enable_memory=enable_memory,
            session_id=raw_session_id,
            case_id=getattr(siemplify, "case_id", None)
        )

        # Initialize the Wiz MCP Toolset (exposes the 'discover' and 'execute' meta-tools)
        wiz_toolset = manager.init_wiz_mcp_toolset(
            client_id=wiz_client_id,
            client_secret=wiz_client_secret,
            data_center=wiz_data_center,
            mcp_url=wiz_mcp_url
        )

        # 5. Build Domain Scoping Context
        domain_context = ""
        if wiz_domains:
            domain_context = (
                f"\nDOMAIN SCOPE:\n- Only discover and execute tools from these Wiz domains: {', '.join(wiz_domains)}.\n"
                f"- If the task cannot be completed within these domains, say so instead of using other domains.\n"
            )

        # 6. Construct Final Specialized Instructions
        agent_instructions = f"""You are a professional Cloud Security Assistant powered by Google ADK, connected to the Wiz cloud security platform.
        Your goal is to solve the user's task using live Wiz data.{domain_context}

        PROCEDURE & CAPABILITIES:
        1. The Wiz MCP server exposes exactly two tools: `discover` and `execute`. Wiz capabilities are organized into domains (e.g. issues, vulnerabilities, soc, cspm, identity, inventory, attack_surface, secrets, dspm, container_security, compliance, docs).
        2. To find a capability, call `discover` with no arguments to list domains, then call `discover` with a `domain` to get that domain's tool names and input schemas. Skip the first step if you already know the domain.
        3. To run a capability, call `execute` with `tool_name` set to an exact tool name returned by `discover` and `parameters` matching that tool's input schema. NEVER guess tool names or parameter names.
        4. Prefer read-only lookups. Do NOT call tools that create, update, ignore, resolve, or delete Wiz objects unless the user explicitly asks for that change.
        5. Keep result sets small: use filters and limits (e.g. `first`) and only paginate when the task requires it.

        Always provide technical reasoning, cite the Wiz identifiers (issue IDs, resource IDs, CVEs) you relied on, and summarize your findings in Markdown."""

        # 7. Execution
        siemplify.LOGGER.info(f"Launching Wiz MCP Agent against: {wiz_mcp_url} (Domains: {wiz_domains or 'all'}, Memory: {enable_memory}, Session: {session_id})")

        all_tools = [wiz_toolset]
        if memory_tools:
            all_tools.extend(memory_tools)

        results = manager.run_agent(
            agent_name="Wiz_MCP_Agent",
            instructions=agent_instructions,
            input_text=user_prompt,
            tools=all_tools,
            thinking_budget=thinking_budget,
            session_id=session_id,
            memory_service=memory_service,
            max_output_tokens=8192
        )

        # 8. Harvest Results
        result_value = results["final_response"]
        output_message = "Wiz MCP Agent successfully finished its task."

        # ADD JSON RESULTS:
        # 1. Programmatic JSON (for playbook placeholders)
        siemplify.result.add_result_json(results)
        # 2. UI-Visible JSON (for the 'Wiz_MCP_Agent_Results' tab on the Case Wall)
        siemplify.result.add_json("Wiz_MCP_Agent_Results", results)

        if results["thoughts"]:
            thought_str = "\n".join([f"• {t}" for t in results["thoughts"]])
            siemplify.add_comment(f"### Agent Reasoning ###\n{thought_str}")

        siemplify.add_comment(f"### Agent Final Response ###\n\n{result_value}")

    except Exception as e:
        output_message = f"Error: {str(e)}"
        siemplify.LOGGER.error(output_message)
        result_value = False
        status = EXECUTION_STATE_FAILED

    siemplify.LOGGER.info(f"Action Finalized. Status: {status}")
    siemplify.end(output_message, result_value, status)

if __name__ == "__main__":
    main()
