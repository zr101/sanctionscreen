from sanctionscreen.normalise import (
    normalise_name,
    normalise_variants,
    strip_leading_honorifics,
    unique_normalised,
)


class TestNormaliseName:
    def test_casefold(self):
        assert normalise_name("MOHAMMED") == "mohammed"

    def test_diacritics_folded(self):
        assert normalise_name("Müller") == "muller"
        assert normalise_name("François Café") == "francois cafe"

    def test_punctuation_to_space(self):
        assert normalise_name("O'Brien") == "o brien"
        assert normalise_name("Al-Qaida") == "al qaida"
        assert normalise_name("Abd al-Rahman, Muhammad") == "abd al rahman muhammad"

    def test_whitespace_collapsed(self):
        assert normalise_name("  Ali \t  Hassan \n") == "ali hassan"

    def test_empty_and_whitespace_only(self):
        assert normalise_name("") == ""
        assert normalise_name("   \t\n ") == ""
        assert normalise_name("...---...") == ""

    def test_arabic_script_preserved(self):
        # Base Arabic letters survive; hamza-carrying alef folds to bare alef
        # (NFKD decomposes it to alef + combining hamza), so hamza spelling
        # variants normalise identically — deliberate, aids matching.
        assert normalise_name("اسامة بن لادن") == "اسامة بن لادن"
        assert normalise_name("أسامة بن لادن") == normalise_name("اسامة بن لادن")

    def test_arabic_diacritics_stripped(self):
        # fatha/damma etc. are combining marks and must fold away.
        assert normalise_name("مُحَمَّد") == normalise_name("محمد")

    def test_cyrillic_casefold(self):
        assert normalise_name("ПУТИН Владимир") == "путин владимир"

    def test_ligatures_decomposed(self):
        # NFKD expands compatibility ligatures.
        assert normalise_name("ﬁnance") == "finance"

    def test_very_long_name(self):
        long_name = ("Abdul " * 500).strip()
        result = normalise_name(long_name)
        assert result == ("abdul " * 500).strip()

    def test_digits_kept(self):
        assert normalise_name("Vessel 21") == "vessel 21"


class TestHonorifics:
    def test_single_honorific_stripped(self):
        assert strip_leading_honorifics("haji abdul manan") == "abdul manan"

    def test_title_with_dot_already_normalised(self):
        assert normalise_name("Dr. Ayman al-Zawahiri") == "dr ayman al zawahiri"
        assert strip_leading_honorifics("dr ayman al zawahiri") == "ayman al zawahiri"

    def test_multiword_honorific(self):
        assert strip_leading_honorifics("al hajj mohammed omar") == "mohammed omar"

    def test_stacked_honorifics(self):
        assert strip_leading_honorifics("mullah haji mohammed") == "mohammed"

    def test_honorific_mid_name_untouched(self):
        assert strip_leading_honorifics("mohammed haji omar") == "mohammed haji omar"

    def test_never_consumes_whole_name(self):
        # "Haji" alone might be a genuine name; stripping must not empty it.
        assert strip_leading_honorifics("haji") == "haji"
        assert strip_leading_honorifics("mullah haji") == "haji"

    def test_al_particle_not_stripped(self):
        # "al" alone is a name particle, not an honorific.
        assert strip_leading_honorifics("al assad") == "al assad"


class TestVariants:
    def test_no_honorific_single_variant(self):
        assert normalise_variants("Ali Hassan") == ["ali hassan"]

    def test_honorific_two_variants(self):
        assert normalise_variants("Haji Abdul Manan") == [
            "haji abdul manan",
            "abdul manan",
        ]

    def test_empty_no_variants(self):
        assert normalise_variants("") == []
        assert normalise_variants("!!!") == []


class TestUniqueNormalised:
    def test_dedup_and_order(self):
        names = ["Ali Hassan", "ALI  HASSAN", "Hassan Ali", ""]
        assert unique_normalised(names) == ["ali hassan", "hassan ali"]
