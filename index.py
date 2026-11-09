import colorsys
import io
import struct
import zlib
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import matplotlib.patheffects as PathEffects
import matplotlib.patches as mpatches
import requests
from dataclasses import dataclass

HEADERS = {'User-Agent': 'brasil-eleicoes/1.0 (https://github.com/marco-carvalho/brasil-eleicoes)'}
ANOS = (2006, 2010, 2014, 2018, 2022, 2026)
COLUNAS = ['NR_TURNO', 'SG_UF', 'CD_CARGO', 'SG_PARTIDO', 'QT_VOTOS_NOMINAIS']

@dataclass(frozen=True)
class ChaveVoto:
  ano: int
  turno: int
  estado: str
  partido: str

@dataclass(frozen=True)
class MembroZip:
  deslocamento: int
  comprimido: int

@dataclass(frozen=True)
class TurnoMapa:
  nome: str
  numero: int
  arquivo: str

TURNOS = (
  TurnoMapa('primeiro turno', 1, 'brasil-eleicoes-primeiro-turno.png'),
  TurnoMapa('segundo turno', 2, 'brasil-eleicoes-segundo-turno.png'),
)

def url_apuracao(ano: int) -> str:
  return f'https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_candidato_munzona/votacao_candidato_munzona_{ano}.zip'

def diretorio_central(url: str) -> bytes:
  response = requests.get(url, headers={**HEADERS, 'Range': 'bytes=-1048576'}, timeout=(30, 120))
  response.raise_for_status()
  faixa = response.headers.get('Content-Range', '')
  if not faixa.startswith('bytes '):
    raise RuntimeError('O TSE não retornou o intervalo do arquivo')
  inicio = int(faixa.split()[1].split('-')[0])
  dados = response.content
  fim_diretorio = dados.rfind(b'PK\x05\x06')
  if fim_diretorio < 0:
    raise RuntimeError('Diretório do arquivo do TSE não encontrado')
  tamanho, deslocamento = struct.unpack_from('<II', dados, fim_diretorio + 12)
  if deslocamento == 0xFFFFFFFF:
    raise RuntimeError('Arquivo ZIP64 do TSE não suportado')
  inicio_relativo = deslocamento - inicio
  if inicio_relativo < 0 or inicio_relativo + tamanho > len(dados):
    raise RuntimeError('Diretório do arquivo do TSE incompleto')
  return dados[inicio_relativo:inicio_relativo + tamanho]

def entrada_br(diretorio: bytes) -> MembroZip:
  posicao = 0
  while posicao + 46 <= len(diretorio) and diretorio[posicao:posicao + 4] == b'PK\x01\x02':
    comprimido = struct.unpack_from('<I', diretorio, posicao + 20)[0]
    tamanho_nome, tamanho_extra, tamanho_comentario = struct.unpack_from('<HHH', diretorio, posicao + 28)
    deslocamento = struct.unpack_from('<I', diretorio, posicao + 42)[0]
    nome = diretorio[posicao + 46:posicao + 46 + tamanho_nome].decode()
    if nome.rsplit('/', 1)[-1].endswith('_BR.csv'):
      if comprimido == 0xFFFFFFFF or deslocamento == 0xFFFFFFFF:
        raise RuntimeError(f'Arquivo ZIP64 do TSE não suportado: {nome}')
      return MembroZip(deslocamento, comprimido)
    posicao += 46 + tamanho_nome + tamanho_extra + tamanho_comentario
  raise RuntimeError('Arquivo presidencial do TSE não encontrado')

def baixar_membro(url: str, membro: MembroZip) -> bytes:
  fim = membro.deslocamento + membro.comprimido + 512
  response = requests.get(url, headers={**HEADERS, 'Range': f'bytes={membro.deslocamento}-{fim}'}, timeout=(30, 120))
  response.raise_for_status()
  dados = response.content
  if dados[:4] != b'PK\x03\x04':
    raise RuntimeError('Cabeçalho do arquivo do TSE inválido')
  metodo = struct.unpack_from('<H', dados, 8)[0]
  tamanho_nome, tamanho_extra = struct.unpack_from('<HH', dados, 26)
  inicio = 30 + tamanho_nome + tamanho_extra
  conteudo = dados[inicio:inicio + membro.comprimido]
  if len(conteudo) != membro.comprimido:
    raise RuntimeError('Download da apuração do TSE incompleto')
  if metodo == 0:
    return conteudo
  if metodo == 8:
    return zlib.decompress(conteudo, -15)
  raise RuntimeError(f'Compressão do TSE não suportada: {metodo}')

def votos_presidente(bloco: pd.DataFrame, siglas: set[str]) -> pd.DataFrame:
  cargo = bloco['CD_CARGO'].str.strip().str.lstrip('0')
  uf = bloco['SG_UF'].str.strip()
  presidente = bloco.loc[cargo.eq('1') & uf.isin(siglas), ['NR_TURNO', 'SG_UF', 'SG_PARTIDO', 'QT_VOTOS_NOMINAIS']].copy()
  presidente['SG_UF'] = presidente['SG_UF'].str.strip()
  presidente['SG_PARTIDO'] = presidente['SG_PARTIDO'].str.strip()
  presidente['QT_VOTOS_NOMINAIS'] = pd.to_numeric(presidente['QT_VOTOS_NOMINAIS'], errors='coerce').fillna(0).astype(int)
  return presidente.groupby(['NR_TURNO', 'SG_UF', 'SG_PARTIDO'], as_index=False)['QT_VOTOS_NOMINAIS'].sum()

def somar(totais: dict[ChaveVoto, int], ano: int, agrupado: pd.DataFrame) -> None:
  for linha in agrupado.itertuples(index=False):
    chave = ChaveVoto(ano, int(linha.NR_TURNO), linha.SG_UF, linha.SG_PARTIDO)
    totais[chave] = totais.get(chave, 0) + int(linha.QT_VOTOS_NOMINAIS)

def ler_ano(ano: int, siglas: set[str], totais: dict[ChaveVoto, int]) -> None:
  url = url_apuracao(ano)
  conteudo = baixar_membro(url, entrada_br(diretorio_central(url)))
  apuracao = pd.read_csv(io.BytesIO(conteudo), sep=';', encoding='latin-1', dtype=str, usecols=COLUNAS)
  somar(totais, ano, votos_presidente(apuracao, siglas))

def anos_completos(totais: dict[ChaveVoto, int], siglas: set[str], turno: TurnoMapa) -> tuple[int, ...]:
  anos = []
  for ano in ANOS:
    presentes = {chave.estado for chave in totais if chave.ano == ano and chave.turno == turno.numero}
    if not presentes:
      continue
    faltando = siglas - presentes
    if faltando:
      raise RuntimeError(f'Apuração do TSE incompleta em {ano}, {turno.nome}: {sorted(faltando)}')
    anos.append(ano)
  return tuple(anos)

def vitorias_pt(totais: dict[ChaveVoto, int], turno: TurnoMapa, nomes: dict[str, str], anos: tuple[int, ...]) -> pd.DataFrame:
  por_estado: dict[tuple[int, str], dict[str, int]] = {}
  for chave, votos in totais.items():
    if chave.turno != turno.numero or chave.ano not in anos:
      continue
    por_estado.setdefault((chave.ano, chave.estado), {})[chave.partido] = votos
  linhas = []
  for (ano, sigla), partidos in por_estado.items():
    votos_pt = partidos.get('PT', 0)
    maior_outro = max((votos for partido, votos in partidos.items() if partido != 'PT'), default=0)
    linhas.append({'Ano': ano, 'Estado': nomes[sigla], 'Vitoria': int(votos_pt > maior_outro)})
  return pd.DataFrame(linhas)

def malha_estados() -> gpd.GeoDataFrame:
  response = requests.get('https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2025/Brasil/BR_UF_2025.zip', headers=HEADERS, timeout=60)
  response.raise_for_status()
  return gpd.read_file(io.BytesIO(response.content))

def canal_linear(canal: float) -> float:
  if canal <= 0.04045:
    return canal / 12.92
  return ((canal + 0.055) / 1.055) ** 2.4

def luminancia(cor: tuple[float, float, float, float]) -> float:
  vermelho, verde, azul = (canal_linear(canal) for canal in cor[:3])
  return 0.2126 * vermelho + 0.7152 * verde + 0.0722 * azul

def cor_rotulo(cor: tuple[float, float, float, float]) -> str:
  if luminancia(cor) >= 0.18:
    return 'black'
  return 'white'

def escala_vitorias(quantidade: int) -> ListedColormap:
  arco_iris = tuple(
    colorsys.hsv_to_rgb((6 - indice) / 7, 1, 1)
    for indice in range(7)
  )
  return ListedColormap(arco_iris[:quantidade])

def plotar_mapa(eleicoes: pd.DataFrame, estados: gpd.GeoDataFrame, turno: TurnoMapa) -> None:
  fig, ax = plt.subplots(1, figsize=(12, 10))
  cores = escala_vitorias(int(eleicoes['Ano'].nunique()) + 1)
  legend_patches = []
  for _, row in estados.iterrows():
    state_name = row['NM_UF']
    eleicoes_loop_estado = eleicoes[eleicoes['Estado'] == state_name]
    number_of_pt_wins = int(eleicoes_loop_estado['Vitoria'].sum())
    color = tuple(float(canal) for canal in cores(number_of_pt_wins))
    rotulo = cor_rotulo(color)
    label = f'{number_of_pt_wins} vitórias do PT'
    patch = mpatches.Patch(color=color, label=label)
    if not any(p.get_label() == label for p in legend_patches):
      legend_patches.append(patch)
    gpd.GeoDataFrame([row], geometry='geometry').plot(ax=ax, color=color, edgecolor='#333333')
    if state_name == 'Distrito Federal':
      continue
    ax.text(
      s=state_name,
      x=row.geometry.centroid.x,
      y=row.geometry.centroid.y,
      horizontalalignment='center',
      fontsize=8,
      fontweight='bold',
      color=rotulo,
      path_effects=[PathEffects.withStroke(linewidth=3, foreground='white' if rotulo == 'black' else 'black')]
    )
  legend_patches.sort(key=lambda x: int(x.get_label().split()[0]))
  ax.set_title(f"Resultados das eleições presidenciais no Brasil no {turno.nome} de {eleicoes['Ano'].min()} até {eleicoes['Ano'].max()}")
  ax.legend(handles=legend_patches)
  fig.tight_layout()
  fig.savefig(turno.arquivo, dpi=300)
  plt.close(fig)

def gerar_mapas() -> None:
  estados = malha_estados()
  nomes = dict(zip(estados['SIGLA_UF'], estados['NM_UF']))
  siglas = set(nomes)
  totais: dict[ChaveVoto, int] = {}
  for ano in ANOS:
    print(f'Baixando apuração do TSE de {ano}', flush=True)
    ler_ano(ano, siglas, totais)
  for turno in TURNOS:
    anos = anos_completos(totais, siglas, turno)
    ausentes = sorted(set(ANOS) - set(anos))
    if ausentes:
      print(f'Dados do {turno.nome} ainda não disponíveis no arquivo de dados abertos do TSE: {ausentes}', flush=True)
    if not anos:
      raise RuntimeError(f'Nenhuma apuração completa para o {turno.nome}')
    plotar_mapa(vitorias_pt(totais, turno, nomes, anos), estados, turno)

if __name__ == '__main__':
  gerar_mapas()
