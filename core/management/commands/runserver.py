"""Servidor de desenvolvimento escutando na rede local por padrão.

O padrão do Django é 127.0.0.1, que só aceita conexões da própria máquina —
abrir o site no celular exige lembrar de digitar o endereço toda vez. Aqui o
padrão passa a ser 0.0.0.0 (todas as interfaces); passar um endereço na linha
de comando continua funcionando e tem preferência, inclusive para voltar ao
comportamento fechado com `runserver 127.0.0.1:8000`.

Herda da versão do staticfiles (a que serve os arquivos estáticos em DEBUG),
que é a que o Django usaria se este arquivo não existisse. Para esta classe
vencer, `core` vem antes de `django.contrib.staticfiles` no INSTALLED_APPS.
"""
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)


class Command(StaticfilesRunserverCommand):
    default_addr = '0.0.0.0'
