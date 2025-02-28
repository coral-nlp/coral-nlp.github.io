import click
import re
import json
import os
import sys
import traceback
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

import jinja2
import requests
from lxml.html.builder import *
from wasabi import msg

from pybtex import textutils
from pybtex.database.input.bibtex import month_names
from pybtex.bibtex.utils import split_name_list, split_tex_string
from pybtex.database import Person, Entry
from pybtex.richtext import Text
from pybtex.database.input.bibtex import DuplicateField
from pybtex.database.input import bibtex

script_dir = os.path.dirname(os.path.abspath(__file__))

max_author_names = 20
max_editor_names = 4

templateLoader = jinja2.FileSystemLoader(searchpath=script_dir + "/templates")
templateEnv = jinja2.Environment(loader=templateLoader)


class BibParser(bibtex.Parser):
    def __init__(self, *args, bib_type="other", **kwargs):
        """
        :param bib_type: "data" or "other"
        """
        super(BibParser, self).__init__(*args, **kwargs)
        self.bib_type = bib_type

    def process_entry(self, entry_type, key, fields):
        entry = Entry(entry_type)

        if key is None:
            key = 'unnamed-%i' % self.unnamed_entry_counter
            self.unnamed_entry_counter += 1

        seen_fields = set()
        for field_name, field_value_list in fields:
            if field_name.lower() in seen_fields:
                self.handle_error(DuplicateField(key, field_name))
                continue

            field_value = textutils.normalize_whitespace(self.flatten_value_list(field_value_list))
            if field_name in self.person_fields:
                for name in split_tex_string(field_value, " *[,]{1} *") if self.bib_type == 'data' else split_name_list(
                        field_value):
                    entry.add_person(Person(name), field_name)
            else:
                entry.fields[field_name] = field_value
            seen_fields.add(field_name.lower())
        self.data.add_entry(key, entry)


def _get_person_name(person, format="latex"):
    return f"{' '.join(n.render_as(format) for n in (person.rich_first_names + person.rich_middle_names + person.rich_last_names))}"


def _format_persons(persons, format="latex", max_persons=9999, type="editors"):
    persons_str = ""
    if format == "latex":
        persons_str = " and ".join(
            f"{' '.join(n for n in p.bibtex_first_names)} {' '.join(n for n in p.last_names)}" for p in persons)
    else:
        if len(persons) == 1:
            persons_str = _get_person_name(persons[0], format)
        elif len(persons) == 2:
            persons_str = _get_person_name(persons[0], format) + " and " + _get_person_name(persons[1], format)
        elif len(persons) >= 2 and len(persons) <= max_persons:
            persons_str = ", ".join(_get_person_name(p, format) for p in persons[:-1]) + ", and " + _get_person_name(persons[-1], format)
        elif len(persons) > max_persons:
            persons_str = _get_person_name(persons[0], format) + (" et al." if type == "editors" else " et al")

    return persons_str


def _get_raw_bib_entry(item):
    month_names_inv = {v: k for k, v in month_names.items()}
    fields_string = ""
    for key, value in sorted(item.fields.items()):
        if key in [
                    'annote',
                    'arxivpassword',
                    'arxivurl',
                    'awardurl',
                    'bibid',
                    'category',
                    'clef-working-notesurl',
                    'codeurl',
                    'data_author',
                    'data_editor',
                    'dataurl',
                    'demourl',
                    'ecir-invited-paperurl',
                    'eventurl',
                    'keywords',
                    'mentor',
                    'options',
                    'options_dict',
                    'publicationsurl',
                    'request',
                    'researchurl',
                    'todo',
                    'videourl',
                    'wikipediaurl'
                  ] or not value or value == "-" or (key != 'url' and key.endswith("url")):
            continue
        spaces = " " * (25 - len(key))
        if key == 'month':
            value = month_names_inv.get(value, 'jan')
        elif key == 'author' or key == 'people':
            value = f"{{{_format_persons(item.persons.get('author', []), 'latex')}}}"
        elif key == 'editor':
            value = f"{{{_format_persons(item.persons.get('editor', []), 'latex')}}}"
        elif key not in ['articleno', 'number', 'numpages', 'pages', 'volume', 'year'] or not value.isnumeric():
            value = f"{{{value}}}"
        fields_string += f"  {key} ={spaces}{value},\n"
    return f"""@{item.original_type}{{{item.key},\n{fields_string[:-2]}\n}}"""


def _request_github(username, repo):
    # authorization = f'token {access_token}'
    headers = {
        "Accept": "application/vnd.github.v3+json",
        # "Authorization": authorization,
    }

    url_api_repos = "https://api.github.com/repos"
    url_api_param_tree = "git/trees/HEAD?recursive=1"
    url_repo_base = f"{url_api_repos}/{username}/{repo}"
    url_repo_tree = f"{url_repo_base}/{url_api_param_tree}"

    response = requests.get(url_repo_tree, headers=headers)
    response.raise_for_status()
    return json.loads(response.content.decode('utf-8'))


def _get_existing_hrefs(username, directory, inventory_url: str = "https://downloads.webis.de/files_list.json"):
    # TODO make inventory adaptive
    existing_hrefs = {}
    
    files_list = requests.get(inventory_url, timeout=120.0).json()
    site = username.split('-')[0]

    existing_hrefs['publications'] = {
        k: v['path'] 
        for k, v in files_list[site][directory].get('papers', {}).items()}
    existing_hrefs['poster'] = {
        k: v['path'] 
        for k, v in files_list[site][directory].get('posters', {}).items()}
    existing_hrefs['slides'] = {
        k: v['path'] 
        for k, v in files_list[site][directory].get('slides', {}).items()}

    return existing_hrefs


def _sort_publication_items(grouped):
    for year, entries in grouped.items():
        try:
            grouped[year] = sorted(entries, key=lambda x: (
                -datetime.strptime(x.fields['month'], "%B").month if 'month' in x.fields and x.fields['month'] else -13,
                re.sub(r"^([0-9])", r"AAAA\1", x.fields['booktitle'] if 'booktitle' in x.fields and x.fields['booktitle'] else "000"),
                x.fields['bibid']))
        except Exception as e:
            print(e)


def _fields_to_text(item):
    try:
        for k, v in item.fields.items():
            if k == "raw" or type(v) is not str:
                continue
            item.fields[k] = v if "url" in k or "html" in k else str(Text.from_latex(v)).replace('">', "&raquo;").replace('"<', "&laquo;")
    except Exception as e:
        print(e)


def publications2html(
        input: Path, 
        web_directory: str = 'publications', 
        web_repo: str = 'downloads',
        web_username: str = 'webis-de') -> str:
    """
    parse the given publications.bib file and return the generated html segment as string. 
    """
    # parse the bib file
    publications = BibParser(encoding='utf-8', bib_type='other').parse_file(input)

    # TODO create action that generates an overview list in the publication when something new is added there
    # repo_tree = _request_github(username=web_username, repo=web_repo)
    # TODO use the generated overview list to get the links
    existing_hrefs = _get_existing_hrefs(web_username, web_directory)

    grouped = {}
    for item in publications.entries.values():
        item.fields['options_dict'] = {}
        if 'options' in item.fields:
            for option_string in item.fields['options'].split(", "):
                k, v = option_string.split("=")
                item.fields['options_dict'][k] = True if v == "true" else False
        if 'download' not in item.fields['options_dict']:
            item.fields['options_dict']['download'] = True
        if 'skipbib' in item.fields['options_dict'] and item.fields['options_dict']['skipbib'] is True:
            continue
        if item.key.startswith("collection-"):
            continue
        if not item.fields['year'] in grouped:
            grouped[item.fields['year']] = []
        item.fields['author'] = _format_persons(item.persons.get('author', []), "text", max_author_names, type="authors")
        item.fields['data_author'] = ",".join([_get_person_name(person, "text") for person in item.persons.get('author', [])])
        if 'editor' in item.persons:
            max_names = max_editor_names
            if item.type in ["incollection", "proceedings"]:
                max_names = 9999999
            item.fields['editor'] = _format_persons(item.persons.get('editor', []), "text", max_names)
            item.fields['data_editor'] = ",".join([_get_person_name(person, "text") for person in item.persons.get('editor', [])])
        item.fields['bibid'] = item.key.replace(":", "_")
        item.fields['raw'] = _get_raw_bib_entry(item)
        if 'url' in item.fields:
            item.fields['publisherurl'] = item.fields['url']
        item.fields['title'] = re.sub("\\\\sc ", "", item.fields['title'].translate(str.maketrans('', '', '{}')))
        for resource_type in ["publications", "poster", "slides"]:
            href = existing_hrefs[resource_type].get(item.key.replace(':', '_'), False)
            if href:
                item.fields[resource_type + '_href'] = "https://downloads.webis.de" + "/" + href

        artifacts = [re.sub("_href$", "", re.sub("url$", "", field_name)) for field_name in item.fields if (field_name in ["doi", "poster_href", "slides_href"] or (field_name.endswith("url") and field_name != "url")) and item.fields[field_name] != ""]
        if len(artifacts) > 0:
            item.fields['artifacts'] = ",".join(artifacts)

        urls = {re.sub("-", " ", re.sub("_href$", "", re.sub("url$", "", field_name))) if field_name != "url" else field_name: value 
        for field_name, value in item.fields.items() if (field_name in ["doi", "poster_href", "slides_href"] or (field_name.endswith("url"))) and item.fields[field_name] != ""}
        urls["bib-toggle"] = None
        urls["copylink"] = None
        item.fields['urls'] = OrderedDict(sorted(urls.items()))

        grouped[item.fields['year']].insert(0, item)
        _fields_to_text(item)

    _sort_publication_items(grouped)

    t = templateEnv.get_template("publications.html.jinja2")

    try:
        output = t.render(bib_entries=OrderedDict(sorted(grouped.items(), reverse=True)).items())
    except jinja2.exceptions.UndefinedError as e:
        msg.error("Error in: " + str(e), exc_info=True)
        traceback.print_exc()

    return output


@click.command()
@click.argument('input', type=click.Path(file_okay=True, dir_okay=False))
@click.argument('output', type=click.Path(file_okay=True, dir_okay=False))
def main(input: str, output: str):
    """
    Parse a bibtex file (INPUT) with publications and generate a corresponding html snippet (OUTPUT). 

    usage: python3 bib2html.py INPUT_PATH OUTPUT_PATH

    """    
    open(output, 'w').write("{% raw %}\n" + publications2html(input) + "\n{% endraw %}")


if __name__ == '__main__':
    main()
