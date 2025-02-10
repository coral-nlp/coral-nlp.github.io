# Website for the CORAL Project

https://coral-project.github.io

### Build the Website Locally

1. Install Ruby and Jekyll: https://jekyllrb.com/docs/installation/
2. Go to `src/`
3. Run: `bundle exec jekyll serve --baseurl=""`   
4. Go to `http://localhost:4000`

### Add or Change Project Members

1. Add image to `src/img/people`
2. Add a card to `src/_includes/people-cards/<name>.html`
3. Include/exclude the card on the page: `{% include people-cards/<name>.html %}`

### Links
 - Github Pages with Jekyll: https://docs.github.com/en/pages/setting-up-a-github-pages-site-with-jekyll
 - Local Setup: https://docs.github.com/en/pages/setting-up-a-github-pages-site-with-jekyll/testing-your-github-pages-site-locally-with-jekyll
 - Publishing Configuration: https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site