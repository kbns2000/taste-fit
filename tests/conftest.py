import os

os.environ["QLOO_MODE"] = "mock"
os.environ["LLM_MODE"] = "off"
os.environ.pop("QLOO_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
