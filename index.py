import pandas as pd
import zipfile
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.patheffects as PathEffects
import matplotlib.patches as mpatches
import requests
import io

def get_dados_eleicao(url, index_table, year, index_column, left, right):
  eleicao = pd.read_html(url, flavor='bs4')[index_table][:27].iloc[: , :8]
  eleicao.insert(0, 'Ano', year)
  eleicao.rename(columns={f'Estado[{index_column}]':'Estado'}, inplace=True)
  eleicao.rename(columns={left:'PT'}, inplace=True)
  eleicao.rename(columns={right:'Oposição'}, inplace=True)
  return eleicao

eleicoes = pd.concat([
  get_dados_eleicao("https://pt.wikipedia.org/wiki/Elei%C3%A7%C3%A3o_presidencial_no_Brasil_em_2006", 9, 2006, 7, 'Lula', 'Alckmin'),
  get_dados_eleicao("https://pt.wikipedia.org/wiki/Resultados_da_elei%C3%A7%C3%A3o_presidencial_no_Brasil_em_2010", 4, 2010, 3, 'Dilma', 'Serra'),
  get_dados_eleicao("https://pt.wikipedia.org/wiki/Resultados_da_elei%C3%A7%C3%A3o_presidencial_no_Brasil_em_2014", 4, 2014, 4, 'Dilma', 'Aécio'),
  get_dados_eleicao("https://pt.wikipedia.org/wiki/Resultados_da_elei%C3%A7%C3%A3o_presidencial_no_Brasil_em_2018", 7, 2018, 3, 'Haddad', 'Bolsonaro'),
  get_dados_eleicao("https://pt.wikipedia.org/wiki/Resultados_da_elei%C3%A7%C3%A3o_presidencial_no_Brasil_em_2022", 2, 2022, 14, 'Lula', 'Bolsonaro')
])
eleicoes['Eleitorado'] = eleicoes['Eleitorado'].str.split().str.join('.').str.replace('.','').astype(int)
eleicoes['Abstenção'] = eleicoes['Abstenção'].str.split().str.join('.').str.replace('.','').astype(int)
eleicoes.rename(columns={'%':'%Abstenção'}, inplace=True)
eleicoes['%Abstenção'] = eleicoes['Abstenção']/eleicoes['Eleitorado']
eleicoes['PT'] = eleicoes['PT'].str.split().str.join('.').str.replace('.','').astype(int)
eleicoes.rename(columns={'%.1':'%PT'}, inplace=True)
eleicoes['%PT'] = eleicoes['PT']/eleicoes['Eleitorado']
eleicoes['Oposição'] = eleicoes['Oposição'].str.split().str.join('.').str.replace('.','').astype(int)
eleicoes.rename(columns={'%.2':'%Oposição'}, inplace=True)
eleicoes['%Oposição'] = eleicoes['Oposição']/eleicoes['Eleitorado']
eleicoes['Outros'] = eleicoes['Eleitorado']-eleicoes['Abstenção']-eleicoes['PT']-eleicoes['Oposição']
eleicoes['%Outros'] = eleicoes['Outros']/eleicoes['Eleitorado']
eleicoes.reset_index(drop=True)

response = requests.get("https://biogeo.ucdavis.edu/data/gadm3.6/gpkg/gadm36_BRA_gpkg.zip")
zip_bytes = io.BytesIO(response.content)
with zipfile.ZipFile(zip_bytes, 'r') as z:
  with z.open("gadm36_BRA.gpkg") as gpkg_bytes:
    brazil_states = gpd.read_file(gpkg_bytes, layer='gadm36_BRA_1')

fig, ax = plt.subplots(1, figsize=(15, 15))

gradient_colors = LinearSegmentedColormap.from_list("Custom", [(0, 0, 1), (1, 0, 0)], N=eleicoes['Ano'].nunique()+1)

legend_patches = []
for idx, row in brazil_states.iterrows():
  state_name = row['NAME_1']
  eleicoes_loop_estado = eleicoes[eleicoes['Estado'] == state_name]
  number_of_pt_wins = sum(eleicoes_loop_estado['%PT'] > eleicoes_loop_estado['%Oposição'])
  color = gradient_colors(number_of_pt_wins)
  label = f"{number_of_pt_wins} vitórias do PT"
  patch = mpatches.Patch(color=color, label=label)
  if not any(p.get_label() == label for p in legend_patches):
    legend_patches.append(patch)
  gpd.GeoDataFrame([row], geometry='geometry').plot(ax=ax, color=color, edgecolor='white')
  if state_name == 'Distrito Federal':
    continue
  plt.text(
    s=state_name,
    x=row.geometry.centroid.x,
    y=row.geometry.centroid.y,
    horizontalalignment='center',
    fontsize=6,
    fontweight='bold',
    color='black',
    path_effects=[PathEffects.withStroke(linewidth=3, foreground="white")]
  )

legend_patches.sort(key=lambda x: int(x.get_label().split()[0]))
plt.title(f"Resultados das eleições presidenciais no Brasil no segundo turno de {eleicoes['Ano'].min()} até {eleicoes['Ano'].max()}")
plt.legend(handles=legend_patches)
plt.show()
