# Finanças

App web pessoal para gerir rendimentos, despesas, pagamentos recorrentes, investimentos e custos do carro,
com dashboard, gráficos mensais e estatísticas. Funciona no PC e no telemóvel (pode ser instalada como app).

Stack: Django 5.2 + SQLite, Bootstrap 5 e Chart.js (via CDN).

## Correr localmente (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
$env:DJANGO_DEBUG = '1'
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver
```

Abre http://127.0.0.1:8000 e entra com o utilizador que criaste.

Testes e lint:

```powershell
$env:DJANGO_DEBUG = '1'
.\.venv\Scripts\python manage.py test finance
.\.venv\Scripts\ruff check .
```

## Variáveis de ambiente

| Variável | Para quê |
|---|---|
| `DJANGO_DEBUG` | `1` só em desenvolvimento. Em produção, não definas. |
| `DJANGO_SECRET_KEY` | Obrigatória em produção. Gera com `python -c "import secrets; print(secrets.token_urlsafe(50))"`. |
| `DJANGO_ALLOWED_HOSTS` | Domínios separados por vírgula, ex.: `oteuuser.pythonanywhere.com`. |
| `DJANGO_SSL_REDIRECT` | `0` para desligar o redirecionamento para HTTPS (o PythonAnywhere já o faz). |
| `DJANGO_SIGNUP_ENABLED` | `0` fecha a página de registo (`/register/`). Por omissão está aberta. |

## Publicar no PythonAnywhere (grátis)

1. Cria conta em https://www.pythonanywhere.com (plano *Beginner*, grátis).
2. Põe o código no GitHub (repositório privado) e, numa *Bash console* do PythonAnywhere:
   ```bash
   git clone https://github.com/<tu>/<repo>.git finance
   cd finance
   python3.10 -m venv ~/.venvs/finance
   source ~/.venvs/finance/bin/activate
   pip install -r requirements.txt
   ```
3. Cria o ficheiro `~/finance/.env` (só lá, nunca no git) com:
   ```bash
   export DJANGO_SECRET_KEY='<chave gerada>'
   export DJANGO_ALLOWED_HOSTS='<tu>.pythonanywhere.com'
   export DJANGO_SSL_REDIRECT='0'
   ```
   Depois, na consola: `source .env && python manage.py migrate && python manage.py collectstatic --noinput && python manage.py createsuperuser`
4. Separador **Web** → *Add a new web app* → *Manual configuration* → Python 3.10.
   - *Virtualenv*: `/home/<tu>/.venvs/finance`
   - *Static files*: URL `/static/` → diretório `/home/<tu>/finance/staticfiles`
   - Ativa *Force HTTPS*.
   - Edita o *WSGI configuration file* para ficar assim:
     ```python
     import os, sys
     path = '/home/<tu>/finance'
     sys.path.insert(0, path)
     # Lê as variáveis do .env
     with open(os.path.join(path, '.env')) as f:
         for line in f:
             if line.startswith('export '):
                 key, _, value = line[7:].strip().partition('=')
                 os.environ[key] = value.strip("'\"")
     os.environ['DJANGO_SETTINGS_MODULE'] = 'financeplatform.settings'
     from django.core.wsgi import get_wsgi_application
     application = get_wsgi_application()
     ```
5. Carrega em **Reload**. A app fica em `https://<tu>.pythonanywhere.com`.
6. No plano grátis, tens de carregar em *Run until 3 months from today* no separador Web a cada 3 meses.

**Segurança:** a app fica pública na Internet e o login e o registo não limitam tentativas. Usa uma palavra-passe
longa e única (um gestor de palavras-passe ajuda). Cada conta só vê os seus dados, mas, com o registo aberto,
qualquer pessoa pode criar conta: quando todas as pessoas que vão usar a app já tiverem conta, define
`DJANGO_SIGNUP_ENABLED='0'` no `.env` e faz **Reload**.

**Backups:** a base de dados é o ficheiro `db.sqlite3`. Descarrega-o de vez em quando no separador *Files*.

Para atualizar depois de alterações: `git pull`, `source .env && python manage.py migrate && python manage.py collectstatic --noinput` e **Reload**.

## Instalar no telemóvel

Abre o site no Chrome (Android) → menu ⋮ → *Adicionar ao ecrã principal*.
No iPhone, no Safari → Partilhar → *Adicionar ao ecrã principal*.

## Como usar

- **Categorias**: já vêm algumas criadas (Salário, Supermercado, Telemóvel, Ginásio, Combustível, Portagens…).
  Podes criar as tuas.
- **Transações**: cada rendimento ou despesa. O botão `+` adiciona rápido.
- **Recorrentes**: salário, telemóvel, ginásio, renda… No Dashboard, o botão *Lançar* cria as transações do mês (rendimentos e despesas).
- **Carro**: toca nos dias em que foste de carro para o trabalho; regista os abastecimentos e as portagens
  (entram automaticamente nas despesas). Com os km e o depósito atestado, a app calcula o consumo.
- **Investimentos**: cria os ativos, regista as compras e vendas e, de vez em quando, o valor atual
  para veres o ganho.
