export const seasonFor = month => month<=2?'Winter':month<=5?'Pre-monsoon':month<=9?'Southwest monsoon':'Post-monsoon';
export function detectRegime({temperature=null,rain24=null,wind=null,month,coast_km=null,weather_code=null}) {
  const tags=[],evidence=[];
  const add=(tag,why)=>{tags.push(tag);evidence.push(why);};
  if(Number.isFinite(rain24)&&rain24>64.5)add('Heavy rainfall',`24-hour forecast accumulation ${rain24.toFixed(1)} mm exceeds the prototype 64.5 mm trigger.`);
  if(Number.isFinite(temperature)&&temperature>=40)add('High temperature',`Forecast temperature ${temperature.toFixed(1)} °C exceeds the prototype 40 °C trigger.`);
  if(Number.isFinite(temperature)&&(temperature>=45||temperature<=5))add('Extreme temperature','Forecast crosses a prototype temperature screening threshold.');
  if(Number.isFinite(wind)&&wind>=17.2)add('High wind',`Forecast wind ${wind.toFixed(1)} m/s exceeds the prototype 17.2 m/s trigger.`);
  if([95,96,99].includes(weather_code))add('Convective signal','Provider weather code indicates thunderstorms; no CAPE-based classifier is fitted.');
  if(month>=6&&month<=9)add('Monsoon season','Calendar falls in June–September; this does not establish local monsoon onset.');
  if(Number.isFinite(coast_km)&&coast_km<=50)add('Coastal','Location metadata is within 50 km of the coast.');
  if(!tags.length)add('Normal / unclassified','No available feature crosses a prototype rule. This is not an all-clear.');
  return {primary:tags[0],tags,evidence,method:'Transparent forecast-based rules v1',confidence:null,confidence_note:'No fitted classifier or calibrated regime probability.',not_assessed:['Official heatwave (needs departures/persistence)','Cyclonic circulation (needs track/pressure/vorticity evidence)','Observed dry spell (needs antecedent observations)']};
}
