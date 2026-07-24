import json
from typing import Generator

from openai import OpenAI

from tools import get_all_tool_schemas, execute_tool

class Agent:
    def __init__(
        self,
        model: str = "qwen2.5:7b",
        system_prompt: str | None = None,
        max_iterations: int = 10,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",  # Ollama не проверяет ключ
    ):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.max_iterations = max_iterations
        
        self.system_prompt = system_prompt or (
            "You are a helpful assistant with access to tools."
            "Use them when you need to get up-to-date data or perform actions."
            "Always explain to the user what you're doing."
            "If a tool returns an error, analyze it and try to correct the parameters,"
            "or inform the user about the problem."
            "Respond in English."
        )
        
        self.messages: list[dict] = [
            {"role": "system", "content": self.system_prompt}
        ]

        self.tool_call_log: list[dict] = []
        
    def reset(self):
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.tool_call_log = []
    
    def run(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message})
        
        for iteration in range(self.max_iterations):
            print(f"\n{'='*60}")
            print(f"🔄 Iteration {iteration + 1}/{self.max_iterations}")
            print(f"{'='*60}")
            
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=get_all_tool_schemas(),
                    tool_choice="auto",
                    temperature=0.1,    
                )
            except Exception as e:
                return f"Error on calling LLM: {e}"
            
            assistant_message = response.choices[0].message

            if not assistant_message.tool_calls:
                text_response = assistant_message.content or ""
                self.messages.append({
                    "role": "assistant", 
                    "content": text_response
                })
                print(f"💬 Answer: {text_response}")
                return text_response
            
            assistant_msg_dict = {
                "role": "assistant",
                "content": assistant_message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in assistant_message.tool_calls
                ]
            }
            
            self.messages.append(assistant_msg_dict)
            
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                tool_id = tool_call.id
                tool_args_raw = tool_call.function.arguments
                
                print(f"\nTool call: {tool_name}")
                print(f"   Parameters: {tool_args_raw}")
            
                try:
                    tool_args = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
                except json.JSONDecodeError:
                    tool_args = {}
                
                result = execute_tool(tool_name, tool_args)
                
                print(f"   Result: {result}")
                
                self.tool_call_log.append({
                    "iteration": iteration + 1,
                    "tool": tool_name,
                    "args": tool_args,
                    "result_preview": result[:200]
                })
                
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": result,
                })
                
            warning = (
                    f"Reached limit of iterations: ({self.max_iterations}). "
                    "The agent was unable to complete the task. Please try reformulating your request."
                )
                
        self.messages.append({"role": "assistant", "content": warning})
        return warning
    
    def stream_run(self, user_message: str) -> Generator[str, None, None]:
        self.messages.append({"role": "user", "content": user_message})
        for iteration in range(self.max_iterations):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=get_all_tool_schemas(),
                tool_choice="auto",
                temperature=0.1,
            )
            
            assistant_message = response.choices[0].message
            
            if not assistant_message.tool_calls:
                yield assistant_message.content or ""
                return
            
            assistant_msg_dict = {
                "role": "assistant",
                "content": assistant_message.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function", 
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in assistant_message.tool_calls
                ]
            }
            self.messages.append(assistant_msg_dict)
            
            for tool_call in assistant_message.tool_calls:
                result = execute_tool(tool_call.function.name, tool_call.function.arguments)
                
                yield f"[{tool_call.function.name}] → {result[:150]}...\n"
                
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
        
        yield f"Reached iterations limit: ({self.max_iterations})"
        
def main():
    print("Local agent: (Ollama + Qwen2.5)")
    print("   Tools: web_search, read_file, execute_code,")
    print("                send_email, calculator, get_current_time")
    print("   Enter 'quit' to quit, 'reset' to reset.\n")
    
    agent = Agent(model="qwen2.5:7b", max_iterations=8)
    
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBB!")
            break
        
        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("BB!")
            break
        if user_input.lower() == "reset":
            agent.reset()
            print("History cleared.")
            continue
        
        response = agent.run(user_input)
        print(f"\nAgent: {response}")


if __name__ == "__main__":
    main()