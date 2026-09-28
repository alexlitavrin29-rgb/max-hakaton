"""Synthetic diagnostic: inspect finish reason without retaining model reasoning."""
import asyncio
import json
from pathlib import Path
from openai import AsyncOpenAI
from project.llm.config import LLMSettings
from project.llm.services.rental import CONTRACT


async def main():
    root=Path(__file__).resolve().parents[3]
    config=LLMSettings.from_env(root/'.env')
    messages=[{'role':'system','content':CONTRACT},{'role':'user','content':json.dumps(dict(
        message='нет, не 30, а 25 тысяч',known=dict(city=dict(name='Томск',slug='tomsk',region='Томская область',location_id=657600),budget=30000),
        awaiting=None,pending_city=None,pending_budget=None),ensure_ascii=False)}]
    result=[]
    async with AsyncOpenAI(api_key=config.api_key,base_url=config.base_url,timeout=30,max_retries=0) as client:
        for limit in [1000,4096]:
            response=await client.chat.completions.create(model=config.model,messages=messages,response_format={'type':'json_object'},max_tokens=limit)
            choice=response.choices[0]
            item=dict(limit=limit,finish_reason=choice.finish_reason,content_present=bool(choice.message.content),
                      content=choice.message.content,usage=response.usage.model_dump())
            result.append(item)
            print(json.dumps(item,ensure_ascii=True),flush=True)
    (root/'docs/test-results/housing/token-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':asyncio.run(main())
