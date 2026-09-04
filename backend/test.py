import os
import json
import pandas as pd
from squirrel.modules.agents.preprocessor.TabularDataProcessorAgent import TabularDataProcessorAgent
from squirrel.modules.agents.builder.TabularDataModelBuilderAgent import TabularDataModelBuilderAgent

preprocessing_agent = TabularDataProcessorAgent()
builder_agent = TabularDataModelBuilderAgent(
    target_column="Close"
)

df, plan, summary, execution_report = preprocessing_agent.run(
    data=pd.read_csv("D:/Personal Projects/squirrel/backend/datasets/AAPL.csv"),
)
model = builder_agent.run(
    data=df,
    preprocessing_summary=summary
)

with open("output.json", "w") as f:
    json.dump(
        {
            "plan": plan,
            "summary": summary,
            "execution_report": execution_report,
        },
        f,
        indent=4,
    )

with open("model.json", "w") as f:
    json.dump(model, f, indent=4)

# save dataframe to csv
df.to_csv("output.csv", index=False)