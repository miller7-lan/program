package com.homeapp.profithelper.core

import java.text.Normalizer

class FormulaException(message: String) : Exception(message)

/**
 * 安全公式引擎 —— 与桌面端 core/formula_engine.py 的行为保持一致：
 *  - 只允许数字、分类变量名（可以是中文）、+ - * / 和括号
 *  - 先完整解析，再检查是否用到了不允许的运算（与 Python ast 白名单相同的报错）
 *  - 全角符号、换行会先被规范化（与桌面端 normalize_formula 相同）
 */
object FormulaEngine {

    fun evaluate(formula: String, variables: Map<String, Double>): Double {
        val tree = parse(formula)
        val normalized = variables.mapKeys { Normalizer.normalize(it.key, Normalizer.Form.NFKC) }
        return eval(tree, normalized)
    }

    /** 返回 null 表示公式合法，否则返回错误说明。 */
    fun validate(formula: String): String? = try {
        parse(formula)
        null
    } catch (e: FormulaException) {
        e.message
    }

    fun extractVariables(formula: String): List<String> = try {
        val names = mutableListOf<String>()
        collectNames(parse(formula), names)
        names
    } catch (e: FormulaException) {
        emptyList()
    }

    /**
     * 统一公式书写格式（与桌面端 format_formula 逐字一致）：全角符号转半角，运算符两侧各一个空格，
     * 正负号紧贴数字，括号内侧不留空格。例如 "（营业额-成本）×0.9" → "(营业额 - 成本) * 0.9"。
     * 无法解析的公式只做符号与空白规范化。
     */
    fun format(formula: String): String {
        val text = splitPythonWhitespace(normalizeFormula(formula)).joinToString(" ")
        if (validate(text) != null) return text
        val tokens = try {
            Tokenizer(text).tokenize()
        } catch (e: FormulaException) {
            return text
        }
        val out = StringBuilder()
        var previous: String? = null
        for (token in tokens) {
            if (token.type == T.END) continue
            val piece = token.text
            val isOp = token.type == T.OP
            when {
                isOp && (piece == "+" || piece == "-") && (previous == null || previous == "(" || previous in BINARY_OPS) -> {
                    out.append(piece)
                    previous = piece
                }
                isOp && piece in BINARY_OPS -> {
                    out.append(' ').append(piece).append(' ')
                    previous = piece
                }
                isOp && piece == "(" -> {
                    out.append(piece)
                    previous = piece
                }
                else -> {
                    out.append(piece)
                    previous = "operand"
                }
            }
        }
        return out.toString()
    }

    private val BINARY_OPS = setOf("+", "-", "*", "/")

    /** 与 Python str.split() 相同的空白字符拆分。 */
    private fun splitPythonWhitespace(text: String): List<String> {
        val words = mutableListOf<String>()
        val current = StringBuilder()
        for (ch in text) {
            val space = ch in "\t\n\u000B\u000C\r\u001C\u001D\u001E\u001F \u0085\u00A0\u1680\u2028\u2029\u202F\u205F\u3000" ||
                ch in '\u2000'..'\u200A'
            if (space) {
                if (current.isNotEmpty()) {
                    words += current.toString()
                    current.clear()
                }
            } else {
                current.append(ch)
            }
        }
        if (current.isNotEmpty()) words += current.toString()
        return words
    }

    fun normalizeFormula(formula: String): String {
        val nfkc = Normalizer.normalize(formula, Normalizer.Form.NFKC)
        val builder = StringBuilder(nfkc.length)
        for (ch in nfkc) {
            builder.append(
                when (ch) {
                    '×' -> '*'
                    '÷' -> '/'
                    '−' -> '-'
                    '。' -> '.'
                    '\n', '\r', '\t' -> ' '
                    else -> ch
                }
            )
        }
        return builder.toString()
    }

    // ─────────────────────────────────────────
    // 语法树
    // ─────────────────────────────────────────

    private sealed interface Node
    private data class Num(val value: Double) : Node
    private data class NonNumeric(val repr: String) : Node
    private data class Name(val id: String) : Node
    private data class Unary(val op: Char, val operand: Node) : Node
    private data class Binary(val op: Char, val left: Node, val right: Node) : Node
    /**
     * 能解析但不允许的写法（乘方、取余、比较、函数调用……），名称与 Python ast 节点一致。
     * viaOperator = true 表示 Python 中是“允许的 BinOp/UnaryOp + 不允许的运算符”，运算符位于下一层。
     */
    private data class Disallowed(
        val kind: String,
        val children: List<Node> = emptyList(),
        val viaOperator: Boolean = false,
    ) : Node

    private data class OpMarker(val kind: String) : Node

    private fun syntaxError(detail: String = "写法有误，请检查运算符和括号") = FormulaException("公式语法错误：$detail")

    private fun parse(raw: String): Node {
        val formula = normalizeFormula(raw).trim()
        if (formula.isEmpty()) throw FormulaException("公式不能为空")
        val parser = Parser(Tokenizer(formula).tokenize())
        val tree = parser.parseTop()
        checkAllowed(tree)
        return tree
    }

    private fun disallowed(kind: String) = FormulaException("公式包含不允许的操作（$kind），只支持四则运算和字段变量。")

    /** 按 Python ast.walk 的广度优先顺序检查，报告第一个不允许的节点。 */
    private fun checkAllowed(root: Node) {
        val queue = ArrayDeque<Node>()
        queue.addLast(root)
        while (queue.isNotEmpty()) {
            when (val node = queue.removeFirst()) {
                is OpMarker -> throw disallowed(node.kind)
                is Disallowed -> {
                    if (!node.viaOperator) throw disallowed(node.kind)
                    if (node.children.size == 1) {
                        queue.addLast(OpMarker(node.kind))
                        queue.addLast(node.children[0])
                    } else {
                        queue.addLast(node.children[0])
                        queue.addLast(OpMarker(node.kind))
                        queue.addLast(node.children[1])
                    }
                }
                is Unary -> queue.addLast(node.operand)
                is Binary -> {
                    queue.addLast(node.left)
                    queue.addLast(node.right)
                }
                else -> Unit
            }
        }
    }

    private fun collectNames(node: Node, out: MutableList<String>) {
        when (node) {
            is Name -> out += node.id
            is Unary -> collectNames(node.operand, out)
            is Binary -> {
                collectNames(node.left, out)
                collectNames(node.right, out)
            }
            is Disallowed -> node.children.forEach { collectNames(it, out) }
            else -> Unit
        }
    }

    private fun eval(node: Node, variables: Map<String, Double>): Double = when (node) {
        is Num -> node.value
        is NonNumeric -> throw FormulaException("不支持非数字常量：${node.repr}")
        is Name -> variables[node.id]
            ?: throw FormulaException("变量 '${node.id}' 未在当日记录中定义，请先在录入页填写该字段。")
        is Unary -> {
            val value = eval(node.operand, variables)
            if (node.op == '-') -value else value
        }
        is Binary -> {
            val left = eval(node.left, variables)
            val right = eval(node.right, variables)
            when (node.op) {
                '+' -> left + right
                '-' -> left - right
                '*' -> left * right
                else -> {
                    if (right == 0.0) throw FormulaException("公式出现除以零，请检查数据。")
                    left / right
                }
            }
        }
        is Disallowed -> throw disallowed(node.kind)
        is OpMarker -> throw disallowed(node.kind)
    }

    // ─────────────────────────────────────────
    // 词法分析
    // ─────────────────────────────────────────

    private enum class T { NUM, NONNUM, NAME, KEYWORD, OP, END }

    private data class Token(val type: T, val text: String, val number: Double = 0.0)

    private val KEYWORDS = setOf(
        "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
        "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in",
        "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
    )

    private val MULTI_OPS = listOf("**", "//", "<<", ">>", "<=", ">=", "==", "!=")
    private const val SINGLE_OPS = "+-*/()%@<>~&|^,.[]{}:="

    private class Tokenizer(private val s: String) {
        private var i = 0
        private val brackets = ArrayDeque<Char>()

        fun tokenize(): List<Token> {
            val tokens = mutableListOf<Token>()
            while (i < s.length) {
                val c = s[i]
                when {
                    c == ' ' || c == '\u000C' -> i++
                    c.isDigit() || (c == '.' && i + 1 < s.length && s[i + 1].isDigit()) -> tokens += number()
                    isIdentStart(s.codePointAt(i)) -> tokens += name()
                    else -> tokens += operator()
                }
            }
            if (brackets.isNotEmpty()) throw syntaxError("有括号没有闭合")
            tokens += Token(T.END, "")
            return tokens
        }

        private fun number(): Token {
            val start = i
            if (s[i] == '0' && i + 1 < s.length && s[i + 1] in "xXoObB") {
                val radix = when (s[i + 1]) { 'x', 'X' -> 16; 'o', 'O' -> 8; else -> 2 }
                i += 2
                val digits = StringBuilder()
                while (i < s.length && (s[i].isLetterOrDigit() || s[i] == '_')) {
                    if (s[i] != '_') digits.append(s[i])
                    i++
                }
                val value = digits.toString().toLongOrNull(radix) ?: throw syntaxError("数字后面缺少运算符")
                return Token(T.NUM, s.substring(start, i), value.toDouble())
            }
            val intPart = digitPart()
            var isFloat = false
            if (i < s.length && s[i] == '.') {
                isFloat = true
                i++
                if (i < s.length && s[i].isDigit()) digitPart()
            }
            if (i < s.length && (s[i] == 'e' || s[i] == 'E')) {
                val save = i
                i++
                if (i < s.length && (s[i] == '+' || s[i] == '-')) i++
                if (i < s.length && s[i].isDigit()) {
                    digitPart()
                    isFloat = true
                } else {
                    i = save
                    throw syntaxError("数字后面缺少运算符")
                }
            }
            if (i < s.length && (s[i] == 'j' || s[i] == 'J')) {
                i++
                return Token(T.NONNUM, s.substring(start, i))
            }
            if (i < s.length && isIdentPart(s.codePointAt(i))) throw syntaxError("数字后面缺少运算符")
            if (!isFloat && intPart.length > 1 && intPart[0] == '0' && intPart.any { it != '0' }) {
                throw syntaxError("数字不能以 0 开头")
            }
            val text = s.substring(start, i)
            return Token(T.NUM, text, text.replace("_", "").toDouble())
        }

        private fun digitPart(): String {
            val builder = StringBuilder()
            while (i < s.length && (s[i].isDigit() || (s[i] == '_' && i + 1 < s.length && s[i + 1].isDigit()))) {
                if (s[i] != '_') builder.append(s[i])
                i++
            }
            return builder.toString()
        }

        private fun name(): Token {
            val start = i
            i += Character.charCount(s.codePointAt(i))
            while (i < s.length && isIdentPart(s.codePointAt(i))) i += Character.charCount(s.codePointAt(i))
            val text = Normalizer.normalize(s.substring(start, i), Normalizer.Form.NFKC)
            return Token(if (text in KEYWORDS) T.KEYWORD else T.NAME, text)
        }

        private fun operator(): Token {
            for (op in MULTI_OPS) {
                if (s.startsWith(op, i)) {
                    i += op.length
                    return Token(T.OP, op)
                }
            }
            val c = s[i]
            if (c !in SINGLE_OPS) throw syntaxError()
            when (c) {
                '(', '[', '{' -> brackets.addLast(c)
                ')', ']', '}' -> {
                    val open = brackets.removeLastOrNull() ?: throw syntaxError("多了右括号")
                    if ("([{".indexOf(open) != ")]}".indexOf(c)) throw syntaxError("多了右括号")
                }
            }
            i++
            return Token(T.OP, c.toString())
        }

        private fun isIdentStart(cp: Int) = cp == '_'.code ||
            (Character.isUnicodeIdentifierStart(cp) && !Character.isIdentifierIgnorable(cp))

        private fun isIdentPart(cp: Int) = cp == '_'.code ||
            (Character.isUnicodeIdentifierPart(cp) && !Character.isIdentifierIgnorable(cp))
    }

    // ─────────────────────────────────────────
    // 语法分析（优先级与 Python 一致）
    // ─────────────────────────────────────────

    private class Parser(private val tokens: List<Token>) {
        private var pos = 0
        private val peek get() = tokens[pos]

        private fun isOp(vararg ops: String) = peek.type == T.OP && peek.text in ops
        private fun isKeyword(vararg words: String) = peek.type == T.KEYWORD && peek.text in words
        private fun next() = tokens[pos++]

        fun parseTop(): Node {
            val first = parseTest()
            if (isOp(",")) {
                val items = mutableListOf(first)
                while (isOp(",")) {
                    next()
                    if (peek.type == T.END) break
                    items += parseTest()
                }
                if (peek.type != T.END) throw syntaxError()
                return Disallowed("Tuple", items)
            }
            if (peek.type != T.END) throw syntaxError()
            return first
        }

        // test := or_test ['if' or_test 'else' test]
        private fun parseTest(): Node {
            val body = parseOr()
            if (isKeyword("if")) {
                next()
                val cond = parseOr()
                if (!isKeyword("else")) throw syntaxError()
                next()
                return Disallowed("IfExp", listOf(body, cond, parseTest()))
            }
            return body
        }

        private fun parseOr(): Node {
            var node = parseAnd()
            while (isKeyword("or")) {
                next()
                node = Disallowed("BoolOp", listOf(node, parseAnd()))
            }
            return node
        }

        private fun parseAnd(): Node {
            var node = parseNot()
            while (isKeyword("and")) {
                next()
                node = Disallowed("BoolOp", listOf(node, parseNot()))
            }
            return node
        }

        private fun parseNot(): Node {
            if (isKeyword("not")) {
                next()
                return Disallowed("Not", listOf(parseNot()), viaOperator = true)
            }
            return parseComparison()
        }

        private fun parseComparison(): Node {
            var node = parseBitOr()
            while (isOp("<", ">", "<=", ">=", "==", "!=") || isKeyword("in", "is")) {
                next()
                if (isKeyword("not")) next()
                node = Disallowed("Compare", listOf(node, parseBitOr()))
            }
            return node
        }

        private fun parseBitOr(): Node = binaryChain(::parseBitXor, mapOf("|" to "BitOr"))
        private fun parseBitXor(): Node = binaryChain(::parseBitAnd, mapOf("^" to "BitXor"))
        private fun parseBitAnd(): Node = binaryChain(::parseShift, mapOf("&" to "BitAnd"))
        private fun parseShift(): Node = binaryChain(::parseArith, mapOf("<<" to "LShift", ">>" to "RShift"))

        private fun binaryChain(operand: () -> Node, ops: Map<String, String>): Node {
            var node = operand()
            while (peek.type == T.OP && peek.text in ops) {
                val kind = ops.getValue(next().text)
                node = Disallowed(kind, listOf(node, operand()), viaOperator = true)
            }
            return node
        }

        private fun parseArith(): Node {
            var node = parseTerm()
            while (isOp("+", "-")) {
                val op = next().text[0]
                node = Binary(op, node, parseTerm())
            }
            return node
        }

        private fun parseTerm(): Node {
            var node = parseFactor()
            while (isOp("*", "/", "//", "%", "@")) {
                when (val op = next().text) {
                    "*", "/" -> node = Binary(op[0], node, parseFactor())
                    "//" -> node = Disallowed("FloorDiv", listOf(node, parseFactor()), viaOperator = true)
                    "%" -> node = Disallowed("Mod", listOf(node, parseFactor()), viaOperator = true)
                    else -> node = Disallowed("MatMult", listOf(node, parseFactor()), viaOperator = true)
                }
            }
            return node
        }

        private fun parseFactor(): Node {
            if (isOp("+", "-")) {
                val op = next().text[0]
                return Unary(op, parseFactor())
            }
            if (isOp("~")) {
                next()
                return Disallowed("Invert", listOf(parseFactor()), viaOperator = true)
            }
            return parsePower()
        }

        private fun parsePower(): Node {
            val base = parseTrailers(parseAtom())
            if (isOp("**")) {
                next()
                return Disallowed("Pow", listOf(base, parseFactor()), viaOperator = true)
            }
            return base
        }

        private fun parseTrailers(atom: Node): Node {
            var node = atom
            while (true) {
                node = when {
                    isOp("(") -> {
                        next()
                        val args = mutableListOf<Node>()
                        while (!isOp(")")) {
                            args += parseTest()
                            if (isOp(",")) next() else break
                        }
                        expect(")")
                        Disallowed("Call", listOf(node) + args)
                    }
                    isOp("[") -> {
                        next()
                        val index = parseTest()
                        expect("]")
                        Disallowed("Subscript", listOf(node, index))
                    }
                    isOp(".") -> {
                        next()
                        if (peek.type != T.NAME && peek.type != T.KEYWORD) throw syntaxError()
                        next()
                        Disallowed("Attribute", listOf(node))
                    }
                    else -> return node
                }
            }
        }

        private fun parseAtom(): Node {
            val token = peek
            return when {
                token.type == T.NUM -> Num(next().number)
                token.type == T.NONNUM -> NonNumeric(next().text)
                token.type == T.NAME -> Name(next().text)
                token.type == T.KEYWORD && token.text == "True" -> next().let { Num(1.0) }
                token.type == T.KEYWORD && token.text == "False" -> next().let { Num(0.0) }
                token.type == T.KEYWORD && token.text == "None" -> next().let { NonNumeric("None") }
                isOp("(") -> {
                    next()
                    if (isOp(")")) {
                        next()
                        return Disallowed("Tuple")
                    }
                    val inner = parseTest()
                    if (isOp(",")) {
                        val items = mutableListOf(inner)
                        while (isOp(",")) {
                            next()
                            if (isOp(")")) break
                            items += parseTest()
                        }
                        expect(")")
                        return Disallowed("Tuple", items)
                    }
                    expect(")")
                    inner
                }
                isOp("[") -> {
                    next()
                    val items = mutableListOf<Node>()
                    while (!isOp("]")) {
                        items += parseTest()
                        if (isOp(",")) next() else break
                    }
                    expect("]")
                    Disallowed("List", items)
                }
                else -> throw syntaxError()
            }
        }

        private fun expect(op: String) {
            if (!isOp(op)) throw syntaxError()
            next()
        }
    }
}
