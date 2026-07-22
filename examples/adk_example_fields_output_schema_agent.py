# https://github.com/google/adk-python/blob/v1.21.0/contributing/samples/fields_output_schema/agent.py
from google.adk import Agent
from pydantic import BaseModel


class WeatherData(BaseModel):
  temperature: str
  humidity: str
  wind_speed: str


root_agent = Agent(
    name='root_agent',
    model='gemini-2.0-flash',
    instruction="""\
Answer user's questions based on the data you have.

If you don't have the data, you can just say you don't know.

Here are the data you have for San Jose

* temperature: 26 C
* humidity: 20%
* wind_speed: 29 mph

Here are the data you have for Cupertino

* temperature: 16 C
* humidity: 10%
* wind_speed: 13 mph

""",
    output_schema=WeatherData,
    output_key='weather_data',
)
